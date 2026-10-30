# gateway/pi_can_gateway.py
# SecondPulse gateway service for a Raspberry Pi.
#
# This is our own Python implementation of the translation, inspired by the open source
# Battery Emulator project. It is not a port of that firmware.
#
# It reads native BMS frames from one CAN interface (can0), applies the gateway rules in
# shared/gateway_rules.py, drives the precharge and main contactor relays over GPIO, and
# sends Pylontech style frames on a second CAN interface (can1).
#
# A Linux board is not a safety rated controller. The real protection must be the
# hardware fuse, the battery's own BMS and the contactors. See docs/PI_SETUP.md.
#
# Run modes:
#   live  real can0 and can1 and real GPIO
#   demo  no hardware. Fake BMS frames, vcan or an in memory bus, mock GPIO that never
#         touches a real pin.
#
#   python -m gateway.pi_can_gateway --mode demo
#   python -m gateway.pi_can_gateway --mode live --require-gpio
import argparse
import json
import logging
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, List, Optional

import can

from gateway import buses as bus_mod
from gateway.config import Config, load_config
from gateway.fake_bms import DemoScenario, FakeBms
from gateway.gpio import RelayBoard, init_gpio
from gateway.profiles import BmsSnapshot, get_profile
from gateway.timebase import Timebase
from shared import can_frames as cf
from shared import gateway_rules as rules

log = logging.getLogger("gateway")

TARGET = "Solar inverter (Pylontech style CAN)"
CONTACTOR_NAMES = {rules.STATE_DISABLED: "OPEN", rules.STATE_PRECHARGE: "PRECHARGING", rules.STATE_RUN: "CLOSED"}


class PiCanGateway:
    """The gateway logic around one tick. No threads of its own, so tests can drive it by hand."""

    def __init__(
        self,
        cfg: Config,
        buses: bus_mod.BusSet,
        relays: RelayBoard,
        timebase: Timebase,
        source: str,
        gpio_label: str,
        enable_fn: Callable[[], bool],
        iso_fn: Callable[[], bool],
    ):
        self.cfg = cfg
        self.buses = buses
        self.relays = relays
        self.timebase = timebase
        self.source = source
        self.gpio_label = gpio_label
        self._enable_fn = enable_fn
        self._iso_fn = iso_fn
        self.profile = get_profile(cfg.bms.profile)
        self.pack = rules.PackParams(
            nominal_kwh=cfg.pack.nominal_kwh,
            nominal_voltage=cfg.pack.nominal_voltage,
            cells_series=cfg.pack.cells_series,
            c_rate=cfg.pack.c_rate,
        )
        self.snap = BmsSnapshot()
        self._lock = threading.Lock()
        self._rx: Dict[int, dict] = {}
        self._last_rx_ms: Optional[int] = None
        self._enable_prev = False
        self._iso_last_active_ms: Optional[int] = None
        self._enable_since_ms: Optional[int] = None
        self._last_tx_ms: Optional[int] = None
        self._tx_count = 0
        self._tx_errors = 0
        self._rx_count = 0
        self._last_error = ""
        self._inverter_seen = 0
        self._inverter_last: Dict[int, str] = {}
        self._out: Optional[rules.GatewayOutputs] = None
        self._published: Dict = {"source": self.source, "state": "STARTING"}

    # ----- inputs -----
    def _drain_rx(self, now_ms: int) -> None:
        while True:
            msg = self.buses.oem.recv(timeout=0)
            if msg is None:
                return
            data = bytes(msg.data)
            summary = self.profile.decode(msg.arbitration_id, data, self.snap)
            if summary is None:
                continue
            self._last_rx_ms = now_ms
            self._rx_count += 1
            prev = self._rx.get(msg.arbitration_id, {})
            self._rx[msg.arbitration_id] = {
                "can_id": f"0x{msg.arbitration_id:03X}",
                "source": f"BMS ({self.profile.name} profile)",
                "payload_hex": cf.hex_bytes(data),
                "decoded": summary,
                "note": "frames seen: " + str(prev.get("count", 0) + 1),
                "count": prev.get("count", 0) + 1,
                "seen_ms": now_ms,
            }

    def note_inverter_frame(self, msg: can.Message) -> None:
        """Demo only: a frame arrived on the inverter side bus, as a real inverter would receive it."""
        try:
            text = cf.describe_frame(msg.arbitration_id, bytes(msg.data))
        except ValueError:
            return
        with self._lock:
            self._inverter_seen += 1
            self._inverter_last[msg.arbitration_id] = text

    # ----- one tick -----
    def step(self) -> None:
        now = self.timebase.now_ms()
        self._drain_rx(now)

        enable = bool(self._enable_fn())
        if enable and not self._enable_prev:
            self._enable_since_ms = now
        if not enable:
            self._enable_since_ms = None
        self._enable_prev = enable
        ms_since_enable = 0 if self._enable_since_ms is None else now - self._enable_since_ms

        iso_pulse = bool(self._iso_fn())
        if iso_pulse:
            self._iso_last_active_ms = now
        # The BMS isolation flag lags the inverter test, so keep masking for a short grace time
        iso_masking = iso_pulse or (
            self._iso_last_active_ms is not None and now - self._iso_last_active_ms <= self.cfg.bms.iso_mask_grace_ms
        )
        stale = (
            not self.snap.complete
            or self._last_rx_ms is None
            or (now - self._last_rx_ms) > self.cfg.bms.timeout_ms
        )
        out = rules.evaluate(
            self.pack,
            rules.GatewayInputs(
                soc_pct=self.snap.soc_pct,
                soh_pct=self.snap.soh_pct,
                temp_c=self.snap.temp_c,
                pack_voltage_v=self.snap.pack_voltage_v,
                delta_mv=self.snap.delta_mv,
                current_a=self.snap.current_a,
                enable_high=enable,
                ms_since_enable=ms_since_enable,
                iso_pulse_active=iso_masking,
                iso_fault_reported=self.snap.iso_fault,
                bms_stale=stale,
            ),
        )
        self.relays.apply(out.precharge_relay, out.main_relay)

        if out.tx_enabled and (self._last_tx_ms is None or now - self._last_tx_ms >= self.cfg.can.tx_period_ms):
            self._send(out.frames)
            self._last_tx_ms = now

        self._out = out
        self._publish(now, out, enable, iso_pulse, stale)

    def _send(self, frames) -> None:
        for can_id, data in frames:
            try:
                self.buses.inverter.send(can.Message(arbitration_id=can_id, data=data, is_extended_id=False), timeout=0.05)
                self._tx_count += 1
            except can.CanError as exc:
                self._tx_errors += 1
                self._last_error = str(exc)
                log.warning("CAN send failed: %s", exc)

    # ----- published state -----
    def _publish(self, now: int, out: rules.GatewayOutputs, enable: bool, iso_pulse: bool, stale: bool) -> None:
        snap = self.snap
        if out.state == rules.STATE_PRECHARGE:
            detail = f"CAN TX held until {rules.PRECHARGE_HOLD_MS} ms after enable"
        elif out.state == rules.STATE_RUN:
            detail = "HV bus active"
        else:
            detail = "Enable signal low, relays open"
        iso_fault_active = snap.iso_fault and not out.iso_masked
        if out.iso_masked:
            iso_text = "OEM isolation message masked during inverter isolation pulse"
        elif iso_fault_active:
            iso_text = "BMS isolation fault reported and not masked"
        else:
            iso_text = "Normal, no pulse active"

        if out.tx_enabled:
            tx_status = "ACTIVE" if self._last_tx_ms is None else f"ACTIVE (last sent {now - self._last_tx_ms} ms ago)"
        else:
            tx_status = f"HELD (state {out.state})"
        tx_frames = [
            {
                "can_id": f"0x{cid:03X}",
                "target": TARGET,
                "tx_status": tx_status,
                "payload_hex": cf.hex_bytes(data),
                "payload_len": len(data),
                "decoded": cf.describe_frame(cid, data),
            }
            for cid, data in out.frames
        ]
        rx_frames = [
            {k: v for k, v in rec.items() if k not in ("count", "seen_ms")} | {"age_ms": now - rec["seen_ms"]}
            for _, rec in sorted(self._rx.items())
        ]
        state = {
            "source": self.source,
            "bus": self.buses.label,
            "gpio": self.gpio_label,
            "profile": self.profile.name,
            "uptime_ms": now,
            "state": out.state,
            "contactor_state": CONTACTOR_NAMES[out.state],
            "contactor_detail": detail,
            "precharge_relay": out.precharge_relay,
            "main_relay": out.main_relay,
            "enable_high": enable,
            "can_tx_enabled": out.tx_enabled,
            "iso_pulse_active": iso_pulse,
            "isolation_status": iso_text,
            "iso_610h_masked": out.iso_masked,
            "protection_alarm": out.protection_alarm,
            "bms_stale": stale,
            "pack_voltage_v": round(snap.pack_voltage_v, 1),
            "soc_pct": snap.soc_pct,
            "soh_pct": snap.soh_pct,
            "temp_c": snap.temp_c,
            "min_cell_v": round(snap.cell_min_mv / 1000.0, 3),
            "max_cell_v": round(snap.cell_max_mv / 1000.0, 3),
            "cell_delta_mv": int(round(snap.delta_mv)),
            "max_charge_a": out.max_charge_a,
            "max_discharge_a": out.max_discharge_a,
            "cell_voltages": [],
            "oem_rx_frames": rx_frames,
            "inverter_tx_frames": tx_frames,
            "rx_count": self._rx_count,
            "tx_count": self._tx_count,
            "tx_errors": self._tx_errors,
            "last_error": self._last_error,
        }
        with self._lock:
            state["inverter_frames_seen"] = self._inverter_seen
            state["inverter_side_decoded"] = dict(sorted((f"0x{k:03X}", v) for k, v in self._inverter_last.items()))
            self._published = state

    def state(self) -> Dict:
        with self._lock:
            return dict(self._published)


# ---------------------------------------------------------------------------
# Status page: GET /state and GET /health. Read only, no control endpoints.
# ---------------------------------------------------------------------------
def make_handler(gateway: PiCanGateway):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/state", "/api/state"):
                body, code = json.dumps(gateway.state()).encode(), 200
            elif self.path == "/health":
                body, code = b'{"status":"ok"}', 200
            else:
                body, code = b'{"error":"not found"}', 404
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return Handler


def serve_http(gateway: PiCanGateway, host: str, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(gateway))
    threading.Thread(target=server.serve_forever, daemon=True, name="gateway-http").start()
    return server


# ---------------------------------------------------------------------------
# Wiring for the two run modes
# ---------------------------------------------------------------------------
class Runtime:
    def __init__(self, gateway: PiCanGateway, buses: bus_mod.BusSet, relays: RelayBoard, bms: Optional[FakeBms] = None):
        self.gateway = gateway
        self.buses = buses
        self.relays = relays
        self.bms = bms
        self._bg_stop = threading.Event()
        self._threads: List[threading.Thread] = []
        self._closed = False

    def drain_monitor(self) -> None:
        mon = self.buses.inverter_monitor
        while mon is not None:
            msg = mon.recv(timeout=0)
            if msg is None:
                return
            self.gateway.note_inverter_frame(msg)

    def pump_once(self) -> None:
        """One synchronous round for tests: fake BMS sends, gateway ticks, inverter side reads."""
        if self.bms is not None:
            self.bms.send_once()
        self.gateway.step()
        self.drain_monitor()

    def start_background(self) -> None:
        if self.bms is None:
            return

        def monitor_loop():
            while not self._bg_stop.is_set():
                self.drain_monitor()
                self._bg_stop.wait(0.05)

        for target in (lambda: self.bms.run(self._bg_stop), monitor_loop):
            t = threading.Thread(target=target, daemon=True, name="gateway-demo")
            t.start()
            self._threads.append(t)

    def run(self, stop: threading.Event, tick_ms: int = 20, duration_s: Optional[float] = None) -> None:
        self.start_background()
        try:
            while not stop.is_set():
                started = time.monotonic()
                self.gateway.step()
                if duration_s is not None and self.gateway.timebase.now_s() >= duration_s:
                    break
                stop.wait(max(0.0, tick_ms / 1000.0 - (time.monotonic() - started)))
        finally:
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._bg_stop.set()
        for t in self._threads:
            t.join(timeout=1.0)
        self.relays.close()   # relays are switched off before anything else is released
        self.buses.close()


def build_runtime(
    cfg: Config,
    mode: str,
    require_gpio: bool = False,
    force_memory_bus: bool = False,
    timebase: Optional[Timebase] = None,
) -> Runtime:
    tb = timebase or Timebase()
    if mode == "live":
        gpio_label = init_gpio()
        if gpio_label == "mock":
            if require_gpio:
                raise RuntimeError("No usable GPIO and --require-gpio is set. Refusing to start with mock relays.")
            log.error("GPIO is MOCKED. The relays will NOT switch anything in the real world.")
        if cfg.gpio.enable_input_pin < 0:
            raise ValueError("live mode needs gpio.enable_input_pin in the config")
        relays = RelayBoard(cfg.gpio)
        try:
            buses = bus_mod.open_live(cfg)
        except Exception:
            relays.close()
            raise
        enable_fn = lambda: bool(relays.enable_input.value)  # noqa: E731
        iso_fn = (lambda: bool(relays.iso_input.value)) if relays.iso_input is not None else (lambda: False)  # noqa: E731
        gateway = PiCanGateway(cfg, buses, relays, tb, "live", gpio_label, enable_fn, iso_fn)
        return Runtime(gateway, buses, relays)

    if mode == "demo":
        gpio_label = init_gpio(force_mock=True)   # demo never touches a real pin
        relays = RelayBoard(cfg.gpio)
        buses = bus_mod.open_demo(cfg, force_memory=force_memory_bus)
        scenario = DemoScenario(cfg.demo)
        bms = FakeBms(buses.bms_tx, tb, scenario, cells_series=cfg.pack.cells_series)
        gateway = PiCanGateway(
            cfg, buses, relays, tb, "demo", gpio_label,
            enable_fn=lambda: scenario.enable_high(tb.now_s()),
            iso_fn=lambda: scenario.iso_pulse(tb.now_s()),
        )
        return Runtime(gateway, buses, relays, bms)

    raise ValueError(f"unknown mode '{mode}'")


def main() -> None:
    parser = argparse.ArgumentParser(description="SecondPulse CAN gateway for a Raspberry Pi.")
    parser.add_argument("--mode", choices=["live", "demo"], default="demo")
    parser.add_argument("--config", default=None, help="path to a config TOML file")
    parser.add_argument("--port", type=int, default=None, help="override the HTTP status port")
    parser.add_argument("--require-gpio", action="store_true", help="live mode refuses to start with mock GPIO")
    parser.add_argument("--memory-bus", action="store_true", help="demo mode: skip vcan, use the in memory bus")
    parser.add_argument("--run-seconds", type=float, default=None, help="stop after this many seconds")
    parser.add_argument("--tick-ms", type=int, default=20)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg = load_config(args.config)
    rt = build_runtime(cfg, args.mode, require_gpio=args.require_gpio, force_memory_bus=args.memory_bus)
    port = args.port if args.port is not None else cfg.http.port
    server = serve_http(rt.gateway, cfg.http.host, port)
    log.info(
        "gateway running: mode=%s bus=%s gpio=%s status page http://%s:%d/state",
        args.mode, rt.buses.label, rt.gateway.gpio_label, cfg.http.host, server.server_address[1],
    )

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    try:
        rt.run(stop, tick_ms=args.tick_ms, duration_s=args.run_seconds)
    finally:
        server.shutdown()
        final = rt.gateway.state()
        log.info(
            "stopped: state=%s tx_count=%s tx_errors=%s rx_count=%s inverter_frames_seen=%s",
            final.get("state"), final.get("tx_count"), final.get("tx_errors"), final.get("rx_count"),
            final.get("inverter_frames_seen"),
        )


if __name__ == "__main__":
    main()
