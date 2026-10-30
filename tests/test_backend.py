# tests/test_backend.py
import itertools

import numpy as np
from fastapi.testclient import TestClient

from backend import main as m

client = TestClient(m.app)

LEAF = "Nissan Leaf ZE1 (40 kWh)"
JAGUAR = "Jaguar I PACE (90 kWh, Early Revision)"
HEALTHY = dict(cycle_count=300, fast_charge_ratio=0.1, avg_temp_c=20.0, internal_res_mohm=30.0, cell_delta_mv=15.0)


def triage(model, **overrides):
    body = {"oem_model": model, **HEALTHY, **overrides}
    r = client.post("/api/triage", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def gateway(**overrides):
    r = client.post("/api/can-gateway-simulate", json=overrides)
    assert r.status_code == 200, r.text
    return r.json()


def frame(res, can_id):
    return next(f for f in res["inverter_tx_frames"] if f["can_id"] == can_id)


# ===== triage =====

def test_jaguar_always_blocked():
    grid = itertools.product([100, 300, 1500, 3000], [0.0, 0.5], [2.0, 15.0, 40.0])
    for cycles, fast, spread in grid:
        res = triage(JAGUAR, cycle_count=cycles, fast_charge_ratio=fast, cell_delta_mv=spread)
        assert res["tier_number"] == 3
        assert res["safety_status"].startswith("BLOCKED")
        assert res["estimated_second_life_rul_cycles"] == 0
        assert res["capex_eur_per_kwh"] is None
        assert res["cost_savings_pct"] is None


def test_healthy_leaf_is_tier_1():
    res = triage(LEAF)
    assert res["predicted_soh_pct"] >= m.TIER1_MIN_SOH_PCT
    assert res["tier_number"] == 1
    assert res["assigned_tier"].startswith("Tier 1")


def test_tier_boundaries():
    c = m.classify_tier
    assert c(74.0, 45.0, False) == 1
    assert c(73.99, 45.0, False) == 2
    assert c(74.0, 45.01, False) == 2
    assert c(62.0, 100.0, False) == 2
    assert c(61.99, 10.0, False) == 3
    assert c(80.0, 100.01, False) == 3
    assert c(99.0, 2.0, True) == 3


def test_every_input_lands_in_exactly_one_tier():
    sohs = np.arange(45.0, 100.01, 0.5)
    spreads = np.arange(0.0, 250.01, 2.5)
    for soh, spread, defect in itertools.product(sohs, spreads, [False, True]):
        blocked = defect or spread > 100.0 or soh < 62.0
        tier1 = (not blocked) and soh >= 74.0 and spread <= 45.0
        tier2 = (not blocked) and not tier1
        assert [blocked, tier1, tier2].count(True) == 1
        expected = 3 if blocked else (1 if tier1 else 2)
        assert m.classify_tier(float(soh), float(spread), defect) == expected


def test_tier_2_has_its_own_saving():
    tier2 = triage(LEAF, cell_delta_mv=70.0)
    assert tier2["tier_number"] == 2
    capex = m.ASSUMPTIONS["capex_eur_per_kwh"]
    assert tier2["capex_eur_per_kwh"] == capex["tier2"]
    assert tier2["cost_savings_pct"] == round((1 - capex["tier2"] / capex["new_lfp"]) * 100, 1)
    tier1 = triage(LEAF)
    assert tier1["cost_savings_pct"] == round((1 - capex["tier1"] / capex["new_lfp"]) * 100, 1)
    assert tier1["cost_savings_pct"] != tier2["cost_savings_pct"]


def test_unknown_model_is_rejected():
    r = client.post("/api/triage", json={"oem_model": "No Such Pack", **HEALTHY})
    assert r.status_code == 404


# ===== gateway =====

def test_can_tx_held_below_1500_ms_and_released_at_1500():
    for ms in (0, 1000, 1499):
        res = gateway(time_since_enable_ms=ms)
        assert res["can_tx_enabled"] is False
        assert res["max_charge_a"] == 0.0 and res["max_discharge_a"] == 0.0
        assert all(f["tx_status"].startswith("HELD") for f in res["inverter_tx_frames"])
    for ms in (1500, 1501, 4000):
        res = gateway(time_since_enable_ms=ms)
        assert res["can_tx_enabled"] is True
        assert res["max_charge_a"] > 0.0
        assert all(f["tx_status"] == "ACTIVE" for f in res["inverter_tx_frames"])


def test_imbalance_fault_zeroes_both_current_limits():
    res = gateway(time_since_enable_ms=3000, cell_imbalance_fault=True)
    assert res["max_charge_a"] == 0.0
    assert res["max_discharge_a"] == 0.0
    assert res["cell_delta_mv"] > 100
    assert res["protection_alarm"].startswith("CELL_IMBALANCE_CUTOFF")
    limits = m.decode_frame(m.CAN_ID_LIMITS, bytes.fromhex(frame(res, "0x351")["payload_hex"]))
    assert limits["chg_a"] == 0.0 and limits["dis_a"] == 0.0
    alarms = m.decode_frame(m.CAN_ID_ALARMS, bytes.fromhex(frame(res, "0x359")["payload_hex"]))
    assert alarms["prot2"] & m.PROT2_SYSTEM_ERROR


def test_every_payload_is_exactly_8_bytes():
    for model, ms, fault in itertools.product(m.OEM_BATTERY_DB, (0, 2000), (False, True)):
        res = gateway(oem_model=model, time_since_enable_ms=ms, cell_imbalance_fault=fault)
        assert len(res["inverter_tx_frames"]) == 4
        for f in res["inverter_tx_frames"]:
            assert len(bytes.fromhex(f["payload_hex"])) == 8
            assert f["payload_len"] == 8


def test_frame_layouts_round_trip():
    d = m.decode_frame(m.CAN_ID_LIMITS, m.build_frame_351(396.0, 22.9, 22.9, 345.6))
    assert d == {"chg_v": 396.0, "chg_a": 22.9, "dis_a": 22.9, "dis_v": 345.6}
    assert m.build_frame_351(396.0, 22.9, 22.9, 345.6)[:2] == (3960).to_bytes(2, "little")
    d = m.decode_frame(m.CAN_ID_SOC_SOH, m.build_frame_355(78, 81))
    assert d == {"soc": 78, "soh": 81}
    d = m.decode_frame(m.CAN_ID_MEASURE, m.build_frame_356(382.45, -3.2, 24.5))
    assert d == {"volt_v": 382.45, "current_a": -3.2, "temp_c": 24.5}
    raw = m.build_frame_359(0, 0x80, 0, 0)
    assert raw == bytes([0, 0x80, 0, 0, 1, 0x50, 0x4E, 0])


def test_requested_spread_is_what_gets_reported():
    for target in (10, 22, 60, 90):
        res = gateway(cell_delta_mv=target)
        assert abs(res["cell_delta_mv"] - target) <= 2


def test_isolation_mask_follows_the_pulse():
    assert gateway(inverter_isolation_pulse_active=True)["iso_610h_masked"] is True
    assert gateway(inverter_isolation_pulse_active=False)["iso_610h_masked"] is False


# ===== independence and repeatability =====

def test_gateway_is_repeatable_and_does_not_touch_global_numpy_state():
    np.random.seed(123)
    before = np.random.get_state()[1].copy()
    a = gateway()
    b = gateway()
    after = np.random.get_state()[1]
    assert a == b
    assert (before == after).all()
    assert gateway(seed=8)["cell_voltages"] != a["cell_voltages"]


def test_triage_unaffected_by_gateway_calls():
    first = triage(LEAF)
    gateway(seed=99)
    gateway(seed=100, cell_imbalance_fault=True)
    assert triage(LEAF) == first


def test_training_data_is_swappable():
    x, y = m.load_training_data()
    assert x.shape[1] == len(m.FEATURES)
    assert len(x) == len(y)
    model = m.train_soh_model(x, y)
    probe = [300, 0.1, 20.0, 30.0, 15.0]
    assert m.predict_soh_with_spread(model, probe) == m.predict_soh_with_spread(m.soh_model, probe)


# ===== misc endpoints =====

def test_other_endpoints():
    assert client.get("/api/health").json() == {"status": "ok"}
    assert set(client.get("/api/oem-models").json()) == set(m.OEM_BATTERY_DB)
    assert client.get("/api/assumptions").json()["capex_eur_per_kwh"]["new_lfp"] == 165.0
