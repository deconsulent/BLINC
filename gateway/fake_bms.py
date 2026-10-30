# gateway/fake_bms.py
# Generates fake native BMS frames so the whole chain runs without a battery.
#
# Used inside the gateway in demo mode, and also runnable on its own to feed a real or
# virtual CAN interface on a bench:
#   python -m gateway.fake_bms --channel vcan0
#   python -m gateway.fake_bms --channel can0 --spread-mv 120 --iso-fault
# The frames use the made up layout of the demo profile, not a real vehicle layout.
import argparse
import logging
import math
import threading
from typing import List, Optional, Tuple

import can

from gateway.config import DemoConfig, load_config
from gateway.profiles import DemoProfile
from gateway.timebase import Timebase


class DemoScenario:
    """What the demo does over time. Pure functions of elapsed seconds."""

    def __init__(self, cfg: DemoConfig):
        self.cfg = cfg

    @staticmethod
    def _late_in_period(t: float, period: float, length: float) -> bool:
        return period > 0 and (t % period) >= (period - length)

    def enable_high(self, t: float) -> bool:
        return not self._late_in_period(t, self.cfg.enable_period_s, self.cfg.enable_drop_s)

    def iso_pulse(self, t: float) -> bool:
        return self._late_in_period(t, self.cfg.iso_period_s, self.cfg.iso_pulse_s)

    def imbalance(self, t: float) -> bool:
        return self._late_in_period(t, self.cfg.imbalance_period_s, self.cfg.imbalance_len_s)


class FakeBms:
    def __init__(
        self,
        bus: can.BusABC,
        timebase: Timebase,
        scenario: Optional[DemoScenario] = None,
        cells_series: int = 96,
        normal_spread_mv: float = 16.0,
        fault_spread_mv: float = 140.0,
        iso_fault_always: bool = False,
        soh_pct: float = 81.0,
    ):
        self.bus = bus
        self.timebase = timebase
        self.scenario = scenario
        self.cells_series = cells_series
        self.normal_spread_mv = normal_spread_mv
        self.fault_spread_mv = fault_spread_mv
        self.iso_fault_always = iso_fault_always
        self.soh_pct = soh_pct

    def frames_at(self, t: float) -> List[Tuple[int, bytes]]:
        soc = 78.0 + 2.0 * math.sin(t / 20.0)
        temp = 24.2 + 0.5 * math.sin(t / 15.0)
        mean_mv = (3.45 + soc / 100.0 * 0.68) * 1000.0
        spread = self.normal_spread_mv
        iso_fault = self.iso_fault_always
        if self.scenario is not None:
            if self.scenario.imbalance(t):
                spread = self.fault_spread_mv
            # The BMS raises its isolation flag when the inverter runs its isolation test
            iso_fault = iso_fault or self.scenario.iso_pulse(t)
        cell_max = mean_mv + spread / 2.0
        cell_min = mean_mv - spread / 2.0
        pack_v = mean_mv * self.cells_series / 1000.0
        return [
            (DemoProfile.ID_PACK, DemoProfile.encode_pack(pack_v, 0.0, soc, self.soh_pct, iso_fault)),
            (DemoProfile.ID_CELLS, DemoProfile.encode_cells(cell_max, cell_min, temp)),
        ]

    def send_once(self) -> None:
        for can_id, data in self.frames_at(self.timebase.now_s()):
            self.bus.send(can.Message(arbitration_id=can_id, data=data, is_extended_id=False), timeout=0.05)

    def run(self, stop: threading.Event, period_s: float = 0.1) -> None:
        while not stop.is_set():
            try:
                self.send_once()
            except can.CanError as exc:
                logging.getLogger("gateway.fake_bms").warning("send failed: %s", exc)
            stop.wait(period_s)


def main() -> None:
    parser = argparse.ArgumentParser(description="Send fake BMS frames (demo profile) to a CAN interface.")
    parser.add_argument("--interface", default="socketcan")
    parser.add_argument("--channel", default="vcan0")
    parser.add_argument("--spread-mv", type=float, default=16.0, help="cell voltage spread to report")
    parser.add_argument("--iso-fault", action="store_true", help="always set the BMS isolation fault flag")
    parser.add_argument("--cycle", action="store_true", help="follow the demo scenario (imbalance and isolation episodes)")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    cfg = load_config(args.config)
    bus = can.Bus(interface=args.interface, channel=args.channel)
    scenario = DemoScenario(cfg.demo) if args.cycle else None
    bms = FakeBms(
        bus, Timebase(), scenario, cells_series=cfg.pack.cells_series,
        normal_spread_mv=args.spread_mv, iso_fault_always=args.iso_fault,
    )
    stop = threading.Event()
    print(f"Sending fake BMS frames on {args.channel}. Press Ctrl+C to stop.")
    try:
        bms.run(stop)
    except KeyboardInterrupt:
        stop.set()
    finally:
        bus.shutdown()


if __name__ == "__main__":
    main()
