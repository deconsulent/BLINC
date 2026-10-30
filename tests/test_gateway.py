# tests/test_gateway.py
# Gateway tests. None of them need hardware: GPIO is mocked, the CAN bus is in memory and
# the clock is stepped by hand.
import json
import urllib.error
import urllib.request

import pytest
from gpiozero import Device

from backend import main as backend
from gateway import pi_can_gateway as pg
from gateway.config import load_config
from gateway.gpio import RelayBoard, init_gpio
from gateway.profiles import BmsSnapshot, DemoProfile, get_profile
from gateway.timebase import ManualTimebase
from shared import can_frames as cf
from shared import gateway_rules as rules

PACK = rules.PackParams(nominal_kwh=40.0, nominal_voltage=350.0, cells_series=96, c_rate=0.20)


def inputs(**kw):
    base = dict(soc_pct=78.0, soh_pct=81.0, temp_c=24.0, pack_voltage_v=382.0, delta_mv=16.0, ms_since_enable=3000)
    base.update(kw)
    return rules.GatewayInputs(**base)


@pytest.fixture(autouse=True)
def mock_gpio():
    assert init_gpio(force_mock=True) == "mock"
    yield
    try:
        Device.pin_factory.close()
    finally:
        Device.pin_factory = None


def make_runtime():
    cfg = load_config()
    tb = ManualTimebase()
    rt = pg.build_runtime(cfg, "demo", force_memory_bus=True, timebase=tb)
    return cfg, tb, rt


def advance(rt, tb, ms, bms=True, step=20, bms_period=100):
    for i in range(int(ms // step)):
        tb.advance_ms(step)
        if bms and rt.bms is not None and i % (bms_period // step) == 0:
            rt.bms.send_once()
        rt.gateway.step()
        rt.drain_monitor()


def pin_state(cfg, name):
    return Device.pin_factory.pin(getattr(cfg.gpio, name)).state


# ===== rules (pure) =====

def test_hold_below_1500_ms_and_release_at_1500_ms():
    for ms in (0, 1000, 1499):
        out = rules.evaluate(PACK, inputs(ms_since_enable=ms))
        assert out.state == rules.STATE_PRECHARGE
        assert not out.tx_enabled and out.precharge_relay and not out.main_relay
        assert out.max_charge_a == 0.0 and out.max_discharge_a == 0.0
    for ms in (1500, 1501, 9000):
        out = rules.evaluate(PACK, inputs(ms_since_enable=ms))
        assert out.state == rules.STATE_RUN
        assert out.tx_enabled and out.main_relay and not out.precharge_relay
        assert out.max_charge_a == 22.9


def test_disabled_when_enable_is_low():
    out = rules.evaluate(PACK, inputs(enable_high=False, ms_since_enable=9000))
    assert out.state == rules.STATE_DISABLED
    assert not out.tx_enabled and not out.main_relay and not out.precharge_relay


def test_current_derating():
    assert rules.evaluate(PACK, inputs(temp_c=40.0)).max_charge_a == 11.4
    assert rules.evaluate(PACK, inputs(temp_c=4.0)).max_discharge_a == 11.4
    hot_full = rules.evaluate(PACK, inputs(soc_pct=90.0))
    assert hot_full.max_charge_a == 5.7 and hot_full.max_discharge_a == 22.9
    empty = rules.evaluate(PACK, inputs(soc_pct=10.0))
    assert empty.max_charge_a == 22.9 and empty.max_discharge_a == 5.7


def test_imbalance_cutoff_boundary_and_frame_bit():
    ok = rules.evaluate(PACK, inputs(delta_mv=100.0))
    assert ok.protection_alarm == rules.ALARM_NONE and ok.max_charge_a > 0
    bad = rules.evaluate(PACK, inputs(delta_mv=100.5))
    assert bad.protection_alarm == rules.ALARM_IMBALANCE
    assert bad.max_charge_a == 0.0 and bad.max_discharge_a == 0.0
    alarm_frame = dict(bad.frames)[cf.CAN_ID_ALARMS]
    assert cf.decode_frame(cf.CAN_ID_ALARMS, alarm_frame)["prot2"] & cf.PROT2_SYSTEM_ERROR


def test_isolation_flag_is_masked_only_during_the_pulse():
    masked = rules.evaluate(PACK, inputs(iso_fault_reported=True, iso_pulse_active=True))
    assert masked.iso_masked and masked.protection_alarm == rules.ALARM_NONE and masked.max_charge_a > 0
    real = rules.evaluate(PACK, inputs(iso_fault_reported=True, iso_pulse_active=False))
    assert not real.iso_masked and real.protection_alarm == rules.ALARM_ISOLATION and real.max_charge_a == 0.0


def test_stale_bms_trips_protection():
    out = rules.evaluate(PACK, inputs(bms_stale=True))
    assert out.protection_alarm == rules.ALARM_BMS_TIMEOUT
    assert out.max_charge_a == 0.0 and out.max_discharge_a == 0.0


def test_api_simulation_and_gateway_use_the_same_frames():
    req = backend.GatewaySimRequest(time_since_enable_ms=2500, pack_soc_pct=70.0, pack_soh_pct=80.0, pack_temp_c=30.0)
    sim = backend.simulate_can_gateway(req)
    out = rules.evaluate(
        PACK,
        inputs(soc_pct=70.0, soh_pct=80.0, temp_c=30.0, pack_voltage_v=sim.pack_voltage_v,
               delta_mv=sim.cell_delta_mv, ms_since_enable=2500),
    )
    assert [f.payload_hex for f in sim.inverter_tx_frames] == [cf.hex_bytes(d) for _, d in out.frames]


# ===== profile, config, gpio =====

def test_demo_profile_round_trip():
    snap = BmsSnapshot()
    prof = get_profile("demo")
    assert prof.decode(DemoProfile.ID_PACK, DemoProfile.encode_pack(382.4, -1.5, 78.3, 81, True), snap)
    assert prof.decode(DemoProfile.ID_CELLS, DemoProfile.encode_cells(3990, 3974, 24.2), snap)
    assert snap.complete and snap.iso_fault
    assert (snap.pack_voltage_v, snap.current_a, snap.soc_pct, snap.soh_pct) == (382.4, -1.5, 78.3, 81.0)
    assert snap.delta_mv == 16.0 and snap.temp_c == 24.2
    assert prof.decode(0x123, bytes(8), snap) is None
    with pytest.raises(ValueError):
        get_profile("no such profile")


def test_config_defaults_and_validation(tmp_path):
    cfg = load_config()
    assert (cfg.gpio.precharge_relay_pin, cfg.gpio.main_relay_pin) == (17, 27)
    assert cfg.can.oem_channel == "can0" and cfg.can.inverter_channel == "can1"
    bad_key = tmp_path / "a.toml"
    bad_key.write_text("[gpio]\nprecharge_pin = 5\n")
    with pytest.raises(ValueError, match="unknown key"):
        load_config(str(bad_key))
    reserved = tmp_path / "b.toml"
    reserved.write_text("[gpio]\nmain_relay_pin = 25\n")
    with pytest.raises(ValueError, match="reserved"):
        load_config(str(reserved))


def test_gpio_falls_back_to_mock_when_lgpio_is_missing():
    def broken_loader():
        raise ImportError("No module named 'lgpio'")

    assert init_gpio(loader=broken_loader) == "mock"
    assert init_gpio(force_mock=True) == "mock"


def test_relays_are_never_on_together():
    cfg = load_config()
    board = RelayBoard(cfg.gpio)
    with pytest.raises(ValueError):
        board.apply(True, True)
    board.apply(True, False)
    assert (pin_state(cfg, "precharge_relay_pin"), pin_state(cfg, "main_relay_pin")) == (1, 0)
    board.apply(False, True)
    assert (pin_state(cfg, "precharge_relay_pin"), pin_state(cfg, "main_relay_pin")) == (0, 1)
    board.close()


def test_live_mode_refuses_mock_gpio_when_required(monkeypatch):
    monkeypatch.setattr(pg, "init_gpio", lambda *a, **k: "mock")
    with pytest.raises(RuntimeError, match="require-gpio"):
        pg.build_runtime(load_config(), "live", require_gpio=True)


# ===== the whole chain in demo mode =====

def test_chain_holds_tx_then_sends_8_byte_frames():
    cfg, tb, rt = make_runtime()
    try:
        advance(rt, tb, 1480)
        assert rt.gateway.state()["inverter_frames_seen"] == 0
        assert rt.gateway.state()["state"] == "PRECHARGE"
        assert (pin_state(cfg, "precharge_relay_pin"), pin_state(cfg, "main_relay_pin")) == (1, 0)
        advance(rt, tb, 200)
        st = rt.gateway.state()
        assert st["state"] == "RUN" and st["inverter_frames_seen"] >= 4
        assert (pin_state(cfg, "precharge_relay_pin"), pin_state(cfg, "main_relay_pin")) == (0, 1)
        assert len(st["inverter_tx_frames"]) == 4
        assert all(f["payload_len"] == 8 and len(bytes.fromhex(f["payload_hex"])) == 8 for f in st["inverter_tx_frames"])
        assert set(st["inverter_side_decoded"]) == {"0x351", "0x355", "0x356", "0x359"}
        assert st["max_charge_a"] == 22.9 and st["protection_alarm"] == "NONE"
        assert st["source"] == "demo" and st["bus"] == "memory" and st["gpio"] == "mock"
    finally:
        rt.close()


def test_isolation_pulse_is_masked_and_does_not_trip_protection():
    cfg, tb, rt = make_runtime()
    try:
        advance(rt, tb, 8900)      # pulse runs from 8.8 s to 10.0 s
        st = rt.gateway.state()
        assert st["iso_610h_masked"] is True and st["protection_alarm"] == "NONE" and st["max_charge_a"] == 22.9
        alarms = set()
        for _ in range(60):        # pulse end, grace time, and BMS flag falling
            advance(rt, tb, 20)
            alarms.add(rt.gateway.state()["protection_alarm"])
        assert alarms == {"NONE"}
        advance(rt, tb, 1000)
        assert rt.gateway.state()["iso_610h_masked"] is False
    finally:
        rt.close()


def test_isolation_fault_outside_a_pulse_cuts_the_limits():
    cfg, tb, rt = make_runtime()
    try:
        rt.bms.iso_fault_always = True
        advance(rt, tb, 3000)
        st = rt.gateway.state()
        assert st["protection_alarm"].startswith("ISOLATION_FAULT")
        assert st["max_charge_a"] == 0.0 and st["max_discharge_a"] == 0.0
    finally:
        rt.close()


def test_enable_drop_opens_relays_and_restarts_the_hold():
    cfg, tb, rt = make_runtime()
    try:
        advance(rt, tb, 28500)     # enable is low from 28 s to 30 s
        assert rt.gateway.state()["state"] == "DISABLED"
        assert (pin_state(cfg, "precharge_relay_pin"), pin_state(cfg, "main_relay_pin")) == (0, 0)
        advance(rt, tb, 1700)      # enable high again at 30 s
        assert rt.gateway.state()["state"] == "PRECHARGE"
        advance(rt, tb, 1600)
        assert rt.gateway.state()["state"] == "RUN"
    finally:
        rt.close()


def test_imbalance_episode_zeroes_limits_then_recovers():
    cfg, tb, rt = make_runtime()
    try:
        advance(rt, tb, 53000)     # imbalance runs from 52 s to 60 s
        st = rt.gateway.state()
        assert st["protection_alarm"].startswith("CELL_IMBALANCE_CUTOFF")
        assert st["max_charge_a"] == 0.0 and st["max_discharge_a"] == 0.0 and st["cell_delta_mv"] > 100
        advance(rt, tb, 9000)      # imbalance ended at 60 s, enable also came back at 60 s
        st = rt.gateway.state()
        assert st["protection_alarm"] == "NONE" and st["max_charge_a"] == 22.9
    finally:
        rt.close()


def test_silent_bms_trips_the_timeout():
    cfg, tb, rt = make_runtime()
    try:
        advance(rt, tb, 3000)
        assert rt.gateway.state()["protection_alarm"] == "NONE"
        advance(rt, tb, 2500, bms=False)
        st = rt.gateway.state()
        assert st["bms_stale"] is True and st["protection_alarm"].startswith("BMS_TIMEOUT")
        assert st["max_charge_a"] == 0.0
    finally:
        rt.close()


def test_close_switches_the_relays_off():
    cfg, tb, rt = make_runtime()
    advance(rt, tb, 2000)
    assert pin_state(cfg, "main_relay_pin") == 1
    main_pin = Device.pin_factory.pin(cfg.gpio.main_relay_pin)
    rt.close()
    assert main_pin.state == 0


def test_state_has_every_field_the_dashboard_reads():
    cfg, tb, rt = make_runtime()
    try:
        advance(rt, tb, 2000)
        st = rt.gateway.state()
        sim = backend.simulate_can_gateway(backend.GatewaySimRequest()).model_dump()
        assert set(sim) <= set(st)
        assert {"source", "bus", "gpio", "state", "precharge_relay", "main_relay"} <= set(st)
    finally:
        rt.close()


def test_http_status_page():
    cfg, tb, rt = make_runtime()
    server = pg.serve_http(rt.gateway, "127.0.0.1", 0)
    try:
        advance(rt, tb, 2000)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        state = json.load(urllib.request.urlopen(base + "/state", timeout=3))
        assert state["state"] == "RUN" and state["source"] == "demo"
        assert json.load(urllib.request.urlopen(base + "/health", timeout=3)) == {"status": "ok"}
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(base + "/nope", timeout=3)
        assert err.value.code == 404
    finally:
        server.shutdown()
        rt.close()
