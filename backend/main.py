# backend/main.py
# SecondPulse API: battery triage (SOH model plus tier rules) and a CAN gateway simulation.
import struct
from typing import Dict, List, Optional, Tuple

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sklearn.ensemble import RandomForestRegressor

app = FastAPI(
    title="SecondPulse Battery Triage and CAN Gateway API",
    description="Climathon 2026 Track 3: SOH prediction, safety screening and CAN frame simulation.",
    version="1.1.0",
)

# ===========================================================================
# 1. PROJECT ASSUMPTIONS
# Every euro, carbon and hub figure shown anywhere in the app comes from this dict.
# These are project assumptions, not measurements. Sources are listed in the README.
# ===========================================================================
ASSUMPTIONS: Dict = {
    "capex_eur_per_kwh": {
        "tier1": 63.0,
        "tier2": 76.0,
        "teardown": 113.0,
        "new_lfp": 165.0,
    },
    "labor_hours_per_kwh": {
        "tier1": 0.25,
        "tier2": 0.65,
        "teardown": 2.40,
        "new_lfp": 0.15,
    },
    # kg CO2e of manufacturing avoided per usable kWh that is reused instead of new LFP
    "avoided_co2e_kg_per_usable_kwh": 72.0,
    "second_life": {
        "end_of_life_soh_pct": 55.0,
        "fade_pct_per_cycle_tier1": 0.0048,
        "fade_pct_per_cycle_tier2": 0.0060,
        "cycles_per_year_tier1": 320.0,
        "cycles_per_year_tier2": 300.0,
        "max_c_rate": 0.20,
    },
    "hub": {
        "usable_depth_of_discharge": 0.75,
        "solar_kwh_per_kwp_per_day": 3.8,
        "kwh_per_escooter_charge": 0.55,
        "kwh_per_ebike_charge": 0.65,
    },
    "gateway_bom_eur": [
        {
            "item": "Dual CAN ESP32 board",
            "eur": 15.0,
            "role": "Bridges the pack side CAN bus and the inverter side CAN bus.",
        },
        {
            "item": "12 V supply and contactor relay",
            "eur": 22.0,
            "role": "Powers the pack low voltage wake up line and switches the precharge and main contactors.",
        },
        {
            "item": "HV DC fuse and service disconnect in enclosure",
            "eur": 65.0,
            "role": "Overcurrent protection between the pack HV terminals and the inverter.",
        },
    ],
}

# Triage thresholds from the project spec
TIER1_MIN_SOH_PCT = 74.0
TIER1_MAX_SPREAD_MV = 45.0
BLOCK_MAX_SPREAD_MV = 100.0
BLOCK_MIN_SOH_PCT = 62.0

# Project design value for the gateway. Not taken from the Battery Emulator source.
PRECHARGE_HOLD_MS = 1500

TIER_NAMES = {
    1: "Tier 1: Whole Pack Reuse via ESP32 CAN Gateway (Zero Teardown)",
    2: "Tier 2: Module Level 48V Repackaging (Intact Factory Modules)",
    3: "Tier 3: Direct Hydrometallurgical Recycling (Safety / End of Life Gate)",
}


def tier_saving_pct(tier: int) -> Optional[float]:
    """Saving versus new LFP in percent, computed from ASSUMPTIONS. None for Tier 3."""
    capex = ASSUMPTIONS["capex_eur_per_kwh"]
    key = {1: "tier1", 2: "tier2"}.get(tier)
    if key is None:
        return None
    return round((1.0 - capex[key] / capex["new_lfp"]) * 100.0, 1)


# ===========================================================================
# 2. OEM PACK REGISTRY
# The CAN IDs in can_protocol (0x1DB, 0x55B and so on) are reference labels for
# display only. This code does not build or parse OEM side frames.
# ===========================================================================
OEM_BATTERY_DB: Dict[str, Dict] = {
    "Nissan Leaf ZE1 (40 kWh)": {
        "nominal_kwh": 40.0,
        "nominal_voltage": 350.0,
        "cell_series": 96,
        "cell_parallel": 2,
        "chemistry": "NMC532 (laminated pouch)",
        "can_protocol": "Nissan Leaf BMS CAN (0x1DB, 0x1DC, 0x55B, 0x5BC)",
        "defect_flag": False,
        "defect_note": "Needs periodic balancing. Emulator support not yet confirmed by this project.",
    },
    "VW MEB ID.3 / ID.4 (58 kWh)": {
        "nominal_kwh": 58.0,
        "nominal_voltage": 396.0,
        "cell_series": 108,
        "cell_parallel": 2,
        "chemistry": "NMC712 / NMC811",
        "can_protocol": "VW MEB HV BMS CAN (0x1A55540B)",
        "defect_flag": False,
        "defect_note": "Candidate for whole pack reuse if SOH and spread pass the Tier 1 rules.",
    },
    "BMW i3 (94 Ah / 33 kWh)": {
        "nominal_kwh": 33.2,
        "nominal_voltage": 353.0,
        "cell_series": 96,
        "cell_parallel": 1,
        "chemistry": "NMC111 (Samsung SDI prismatic)",
        "can_protocol": "BMW SME CAN (0x112, 0x2F5, 0x431)",
        "defect_flag": False,
        "defect_note": "Prismatic cells in 8 modules. Candidate for Tier 1 or Tier 2.",
    },
    "Stellantis eCMP Peugeot e208 (50 kWh)": {
        "nominal_kwh": 50.0,
        "nominal_voltage": 400.0,
        "cell_series": 108,
        "cell_parallel": 2,
        "chemistry": "NMC622",
        "can_protocol": "Stellantis eCMP BMS CAN",
        "defect_flag": False,
        "defect_note": "Gateway timing and isolation handling not tested on this pack.",
    },
    "Jaguar I PACE (90 kWh, Early Revision)": {
        "nominal_kwh": 90.0,
        "nominal_voltage": 388.0,
        "cell_series": 108,
        "cell_parallel": 4,
        "chemistry": "NMC622 (LG pouch)",
        "can_protocol": "JLR BECM CAN",
        "defect_flag": True,
        "defect_note": (
            "Flagged OEM defect: early pouch tabs may disconnect internally and turn 4P "
            "modules into 3P. Source for this defect still to be confirmed."
        ),
    },
}
DEFAULT_OEM = "Nissan Leaf ZE1 (40 kWh)"


def get_spec(oem_model: str) -> Dict:
    spec = OEM_BATTERY_DB.get(oem_model)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Unknown OEM model: {oem_model}")
    return spec


# ===========================================================================
# 3. SOH MODEL
# Trained on SYNTHETIC data produced by a formula. It shows the pipeline, it does not
# predict real packs. To use real data (for example NASA or CALCE cycling datasets),
# change load_training_data() so it returns (X, y) with the same FEATURES columns.
# ===========================================================================
FEATURES = ["cycle_count", "fast_charge_ratio", "avg_temp_c", "internal_res_mohm", "cell_delta_mv"]
TRAINING_DATA_SOURCE = "synthetic"


def make_synthetic_training_data(n_samples: int = 1200, seed: int = 42) -> Tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    cycles = rng.uniform(200, 2800, n_samples)
    fast_charge = rng.uniform(0.05, 0.85, n_samples)
    temp = rng.uniform(10.0, 42.0, n_samples)
    ir_mohm = 25.0 + 0.018 * cycles + 14.0 * fast_charge + rng.normal(0, 2.5, n_samples)
    delta_mv = 8.0 + 0.025 * cycles + 28.0 * fast_charge + rng.normal(0, 4.0, n_samples)

    x = np.column_stack([cycles, fast_charge, temp, ir_mohm, delta_mv])
    soh = (
        100.0
        - 0.0105 * cycles
        - 8.5 * (fast_charge ** 1.4)
        - 0.22 * np.maximum(0, temp - 25.0)
        - 0.12 * np.maximum(0, ir_mohm - 35.0)
        - 0.08 * np.maximum(0, delta_mv - 20.0)
        + rng.normal(0, 0.8, n_samples)
    )
    return x, np.clip(soh, 45.0, 99.0)


def load_training_data() -> Tuple[np.ndarray, np.ndarray]:
    """The one place that decides where training data comes from."""
    return make_synthetic_training_data()


def train_soh_model(x: np.ndarray, y: np.ndarray, seed: int = 42) -> RandomForestRegressor:
    model = RandomForestRegressor(n_estimators=120, max_depth=10, random_state=seed)
    model.fit(x, y)
    return model


def predict_soh_with_spread(model: RandomForestRegressor, features: List[float]) -> Tuple[float, float, float]:
    """Mean of all trees plus the 5th and 95th percentile of the individual tree outputs.
    The percentiles show disagreement between trees. They are not a statistical confidence interval."""
    x_arr = np.array([features])
    tree_preds = np.array([tree.predict(x_arr)[0] for tree in model.estimators_])
    return (
        round(float(np.mean(tree_preds)), 2),
        round(float(np.percentile(tree_preds, 5)), 2),
        round(float(np.percentile(tree_preds, 95)), 2),
    )


soh_model = train_soh_model(*load_training_data())


# ===========================================================================
# 4. TIER RULES
# Checked in order, so every input lands in exactly one tier:
#   Tier 3 if defect, or spread above 100 mV, or SOH below 62
#   Tier 1 if (not Tier 3) and SOH at least 74 and spread at most 45 mV
#   Tier 2 for everything else
# ===========================================================================
def classify_tier(soh_pct: float, spread_mv: float, defect_flag: bool) -> int:
    if defect_flag or spread_mv > BLOCK_MAX_SPREAD_MV or soh_pct < BLOCK_MIN_SOH_PCT:
        return 3
    if soh_pct >= TIER1_MIN_SOH_PCT and spread_mv <= TIER1_MAX_SPREAD_MV:
        return 1
    return 2


# ===========================================================================
# 5. SCHEMAS
# ===========================================================================
class TriageRequest(BaseModel):
    oem_model: str = Field(..., examples=[DEFAULT_OEM])
    cycle_count: int = Field(1150, ge=50, le=4000)
    fast_charge_ratio: float = Field(0.25, ge=0.0, le=1.0)
    avg_temp_c: float = Field(22.0, ge=-10.0, le=55.0)
    internal_res_mohm: float = Field(48.0, ge=15.0, le=150.0)
    cell_delta_mv: float = Field(24.0, ge=2.0, le=250.0)


class TriageResponse(BaseModel):
    oem_model: str
    tier_number: int
    predicted_soh_pct: float
    soh_ci_90_lower: float  # 5th percentile of tree outputs
    soh_ci_90_upper: float  # 95th percentile of tree outputs
    usable_kwh: float
    estimated_second_life_rul_cycles: int
    estimated_second_life_years: float
    assigned_tier: str
    recommended_application: str
    safety_status: str
    capex_eur_per_kwh: Optional[float]
    new_lfp_benchmark_eur_per_kwh: float
    cost_savings_pct: Optional[float]
    avoided_co2e_kg: float
    training_data_source: str


class GatewaySimRequest(BaseModel):
    oem_model: str = DEFAULT_OEM
    pack_soc_pct: float = Field(78.0, ge=0.0, le=100.0)
    pack_soh_pct: float = Field(81.5, ge=0.0, le=100.0)
    pack_temp_c: float = Field(24.5, ge=-40.0, le=85.0)
    time_since_enable_ms: int = Field(2000, ge=0)
    inverter_isolation_pulse_active: bool = False
    cell_imbalance_fault: bool = False
    cell_delta_mv: float = Field(16.0, ge=0.0, le=400.0)
    seed: int = 7


class OemFrame(BaseModel):
    can_id: str
    source: str
    decoded: str
    note: str


class InverterFrame(BaseModel):
    can_id: str
    target: str
    tx_status: str
    payload_hex: str
    payload_len: int
    decoded: str


class GatewayResponse(BaseModel):
    contactor_state: str
    contactor_detail: str
    can_tx_enabled: bool
    isolation_status: str
    iso_610h_masked: bool
    protection_alarm: str
    pack_voltage_v: float
    min_cell_v: float
    max_cell_v: float
    cell_delta_mv: int
    max_charge_a: float
    max_discharge_a: float
    cell_voltages: List[float]
    oem_rx_frames: List[OemFrame]
    inverter_tx_frames: List[InverterFrame]


# ===========================================================================
# 6. CAN FRAMES (Pylontech style, little endian, 8 bytes each)
# Layout checked against PYLON-LV-CAN.cpp in the Battery Emulator project.
# Differences from that file:
#  * Frames are padded to 8 bytes. The emulator sends shorter frames.
#  * 0x351 bytes 6 and 7 carry the discharge voltage limit, as in the Pylontech documents.
#  * 0x356 voltage is unsigned here. The low voltage spec uses int16, which stops at
#    327.67 V, and these packs run at 350 V and above.
#  * The protocol has no cell imbalance bit. An imbalance cutoff is reported as the
#    system error bit in 0x359 byte 1.
# ===========================================================================
CAN_ID_LIMITS = 0x351
CAN_ID_SOC_SOH = 0x355
CAN_ID_MEASURE = 0x356
CAN_ID_ALARMS = 0x359
PROT2_SYSTEM_ERROR = 0x80
PACK_COUNT = 1


def _u16(value: float) -> int:
    return max(0, min(0xFFFF, int(round(value))))


def _s16(value: float) -> int:
    return max(-0x8000, min(0x7FFF, int(round(value))))


def build_frame_351(chg_v: float, chg_a: float, dis_a: float, dis_v: float) -> bytes:
    return struct.pack("<HHHH", _u16(chg_v * 10), _u16(chg_a * 10), _u16(dis_a * 10), _u16(dis_v * 10))


def build_frame_355(soc_pct: float, soh_pct: float) -> bytes:
    return struct.pack("<HH4x", _u16(soc_pct), _u16(soh_pct))


def build_frame_356(volt_v: float, current_a: float, temp_c: float) -> bytes:
    return struct.pack("<Hhh2x", _u16(volt_v * 100), _s16(current_a * 10), _s16(temp_c * 10))


def build_frame_359(prot1: int, prot2: int, alarm1: int, alarm2: int) -> bytes:
    return struct.pack("<BBBBB2sx", prot1, prot2, alarm1, alarm2, PACK_COUNT, b"PN")


def decode_frame(can_id: int, data: bytes) -> Dict:
    """Decode one of the four frames back to engineering units."""
    if len(data) != 8:
        raise ValueError("expected 8 bytes")
    if can_id == CAN_ID_LIMITS:
        cv, ca, da, dv = struct.unpack("<HHHH", data)
        return {"chg_v": cv / 10, "chg_a": ca / 10, "dis_a": da / 10, "dis_v": dv / 10}
    if can_id == CAN_ID_SOC_SOH:
        soc, soh = struct.unpack("<HH4x", data)
        return {"soc": soc, "soh": soh}
    if can_id == CAN_ID_MEASURE:
        volt, cur, temp = struct.unpack("<Hhh2x", data)
        return {"volt_v": volt / 100, "current_a": cur / 10, "temp_c": temp / 10}
    if can_id == CAN_ID_ALARMS:
        p1, p2, a1, a2, packs, tag = struct.unpack("<BBBBB2sx", data)
        return {"prot1": p1, "prot2": p2, "alarm1": a1, "alarm2": a2, "packs": packs, "tag": tag.decode("ascii")}
    raise ValueError(f"unknown CAN id {can_id:#x}")


def describe_frame(can_id: int, data: bytes) -> str:
    d = decode_frame(can_id, data)
    if can_id == CAN_ID_LIMITS:
        return f"V_chg_max={d['chg_v']:.1f} V, I_chg_max={d['chg_a']:.1f} A, I_dis_max={d['dis_a']:.1f} A, V_dis_min={d['dis_v']:.1f} V"
    if can_id == CAN_ID_SOC_SOH:
        return f"SOC={d['soc']} %, SOH={d['soh']} %"
    if can_id == CAN_ID_MEASURE:
        return f"V_pack={d['volt_v']:.2f} V, I={d['current_a']:.1f} A, T={d['temp_c']:.1f} C"
    return (
        f"prot1={d['prot1']:#04x}, prot2={d['prot2']:#04x}, alarm1={d['alarm1']:#04x}, "
        f"alarm2={d['alarm2']:#04x}, packs={d['packs']}, tag={d['tag']}"
    )


def _hex(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


# ===========================================================================
# 7. ENDPOINTS
# ===========================================================================
@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/oem-models")
def list_oem_models():
    return OEM_BATTERY_DB


@app.get("/api/assumptions")
def get_assumptions():
    return ASSUMPTIONS


@app.post("/api/triage", response_model=TriageResponse)
def evaluate_battery_pack(req: TriageRequest):
    spec = get_spec(req.oem_model)
    features = [req.cycle_count, req.fast_charge_ratio, req.avg_temp_c, req.internal_res_mohm, req.cell_delta_mv]
    soh, ci_low, ci_high = predict_soh_with_spread(soh_model, features)
    usable_kwh = round(spec["nominal_kwh"] * (soh / 100.0), 2)

    tier = classify_tier(soh, req.cell_delta_mv, spec["defect_flag"])
    life = ASSUMPTIONS["second_life"]
    capex = ASSUMPTIONS["capex_eur_per_kwh"]

    if tier == 3:
        if spec["defect_flag"]:
            app_desc = f"Do not deploy in stationary storage. {spec['defect_note']} Route to certified material recovery."
        else:
            app_desc = (
                "Do not deploy in stationary storage. SOH or cell spread is outside the safe reuse range. "
                "Route to certified material recovery."
            )
        safety_status = "BLOCKED: thermal or imbalance risk"
        rul_cycles, rul_years, capex_kwh = 0, 0.0, None
    elif tier == 1:
        app_desc = (
            "Deploy the intact pack behind an ESP32 CAN gateway for off grid solar micromobility "
            "charging hubs or substation peak shaving buffers."
        )
        safety_status = "PASS: approved for whole pack CAN emulation"
        rul_cycles = int(max(0, (soh - life["end_of_life_soh_pct"]) / life["fade_pct_per_cycle_tier1"]))
        rul_years = round(rul_cycles / life["cycles_per_year_tier1"], 1)
        capex_kwh = capex["tier1"]
    else:
        app_desc = (
            "Extract intact 48V factory modules and pair them with a module level BMS for "
            "portable power banks and tool or drone chargers."
        )
        safety_status = "PASS (CONDITIONAL): module level balancing required"
        rul_cycles = int(max(0, (soh - life["end_of_life_soh_pct"]) / life["fade_pct_per_cycle_tier2"]))
        rul_years = round(rul_cycles / life["cycles_per_year_tier2"], 1)
        capex_kwh = capex["tier2"]

    avoided_co2 = round(usable_kwh * ASSUMPTIONS["avoided_co2e_kg_per_usable_kwh"], 1) if tier != 3 else 0.0

    return TriageResponse(
        oem_model=req.oem_model,
        tier_number=tier,
        predicted_soh_pct=soh,
        soh_ci_90_lower=ci_low,
        soh_ci_90_upper=ci_high,
        usable_kwh=usable_kwh,
        estimated_second_life_rul_cycles=rul_cycles,
        estimated_second_life_years=rul_years,
        assigned_tier=TIER_NAMES[tier],
        recommended_application=app_desc,
        safety_status=safety_status,
        capex_eur_per_kwh=capex_kwh,
        new_lfp_benchmark_eur_per_kwh=capex["new_lfp"],
        cost_savings_pct=tier_saving_pct(tier),
        avoided_co2e_kg=avoided_co2,
        training_data_source=TRAINING_DATA_SOURCE,
    )


def _make_cell_voltages(n_series: int, mean_v: float, spread_v: float, seed: int) -> List[float]:
    """Cell voltages scaled so max minus min equals the requested spread."""
    rng = np.random.default_rng(seed)
    noise = rng.normal(size=n_series)
    noise -= noise.mean()
    span = float(noise.max() - noise.min())
    scaled = noise * (spread_v / span) if span > 0 else noise
    return np.round(mean_v + scaled, 3).tolist()


@app.post("/api/can-gateway-simulate", response_model=GatewayResponse)
def simulate_can_gateway(req: GatewaySimRequest):
    """Simulate the translation from an OEM BMS to inverter side CAN frames. No hardware is involved."""
    spec = get_spec(req.oem_model)
    n_series = spec["cell_series"]
    short_name = req.oem_model.split("(")[0].strip()

    spread_mv = max(req.cell_delta_mv, 140.0) if req.cell_imbalance_fault else req.cell_delta_mv
    cell_v_mean = 3.45 + (req.pack_soc_pct / 100.0) * 0.68
    cell_voltages = _make_cell_voltages(n_series, cell_v_mean, spread_mv / 1000.0, req.seed)
    min_cell_v, max_cell_v = min(cell_voltages), max(cell_voltages)
    delta_mv = int(round((max_cell_v - min_cell_v) * 1000))
    pack_voltage = round(sum(cell_voltages), 1)

    # Hold CAN TX until the precharge time has passed
    if req.time_since_enable_ms < PRECHARGE_HOLD_MS:
        contactor_state = "PRECHARGING"
        contactor_detail = f"CAN TX held until {PRECHARGE_HOLD_MS} ms after enable"
        can_tx_enabled = False
        max_charge_a = max_discharge_a = 0.0
    else:
        contactor_state = "CLOSED"
        contactor_detail = "HV bus active"
        can_tx_enabled = True
        base_a = round(
            (spec["nominal_kwh"] * 1000.0 / spec["nominal_voltage"]) * ASSUMPTIONS["second_life"]["max_c_rate"], 1
        )
        temp_derate = 0.5 if req.pack_temp_c > 38.0 or req.pack_temp_c < 5.0 else 1.0
        max_charge_a = round(base_a * temp_derate * (0.25 if req.pack_soc_pct > 88.0 else 1.0), 1)
        max_discharge_a = round(base_a * temp_derate * (0.25 if req.pack_soc_pct < 18.0 else 1.0), 1)

    # The inverter's isolation test and the OEM BMS isolation message collide, so the
    # gateway masks the OEM message while the pulse is active (see Battery Emulator issue 1959).
    iso_masked = req.inverter_isolation_pulse_active
    isolation_status = (
        "OEM isolation message masked during inverter isolation pulse" if iso_masked else "Normal, no pulse active"
    )

    prot2 = 0
    imbalance = req.cell_imbalance_fault or delta_mv > BLOCK_MAX_SPREAD_MV
    if imbalance:
        protection_alarm = "CELL_IMBALANCE_CUTOFF (reported as system error bit 0x80 in 0x359 byte 1)"
        prot2 |= PROT2_SYSTEM_ERROR
        max_charge_a = max_discharge_a = 0.0
    else:
        protection_alarm = "NONE"

    max_charge_v = round(n_series * 4.12, 1)
    min_discharge_v = round(n_series * 3.20, 1)

    status = "ACTIVE" if can_tx_enabled else f"HELD (waiting for {PRECHARGE_HOLD_MS} ms precharge)"
    target = "Solar inverter (Pylontech style CAN)"
    payloads = [
        (CAN_ID_LIMITS, build_frame_351(max_charge_v, max_charge_a, max_discharge_a, min_discharge_v)),
        (CAN_ID_SOC_SOH, build_frame_355(req.pack_soc_pct, req.pack_soh_pct)),
        (CAN_ID_MEASURE, build_frame_356(pack_voltage, 0.0, req.pack_temp_c)),
        (CAN_ID_ALARMS, build_frame_359(0, prot2, 0, 0)),
    ]
    inverter_frames = [
        InverterFrame(
            can_id=f"0x{cid:03X}",
            target=target,
            tx_status=status,
            payload_hex=_hex(data),
            payload_len=len(data),
            decoded=describe_frame(cid, data),
        )
        for cid, data in payloads
    ]

    # Reference labels only. The payload layout of these OEM frames is not modelled.
    oem_note = "Reference label, payload not modelled"
    oem_frames = [
        OemFrame(
            can_id="0x1DB",
            source=f"OEM BMS ({short_name})",
            decoded=f"V_pack={pack_voltage} V, SOC={req.pack_soc_pct:.1f} %, V_cell_max={max_cell_v:.3f} V",
            note=oem_note,
        ),
        OemFrame(
            can_id="0x55B",
            source=f"OEM BMS ({short_name})",
            decoded=f"SOH={req.pack_soh_pct:.1f} %, Cell_delta={delta_mv} mV, T={req.pack_temp_c:.1f} C",
            note=oem_note,
        ),
    ]

    return GatewayResponse(
        contactor_state=contactor_state,
        contactor_detail=contactor_detail,
        can_tx_enabled=can_tx_enabled,
        isolation_status=isolation_status,
        iso_610h_masked=iso_masked,
        protection_alarm=protection_alarm,
        pack_voltage_v=pack_voltage,
        min_cell_v=min_cell_v,
        max_cell_v=max_cell_v,
        cell_delta_mv=delta_mv,
        max_charge_a=max_charge_a,
        max_discharge_a=max_discharge_a,
        cell_voltages=cell_voltages,
        oem_rx_frames=oem_frames,
        inverter_tx_frames=inverter_frames,
    )
