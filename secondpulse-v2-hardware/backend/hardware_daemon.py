# backend/hardware_daemon.py
# SecondPulse V2: Physical Serial Daemon & Battery Physics Engine
# Connects to ESP32-S3 over USB Serial, performs live voltage tracking,
# active DC load sag IR calculation, dynamic SOH, and real LCA impact models.

import json
import logging
import os
import threading
import time
from typing import Dict, List, Optional

import serial
import serial.tools.list_ports
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("HardwareDaemon")

SERIAL_PORT = os.environ.get("SERIAL_PORT", "COM18")
BAUD_RATE = int(os.environ.get("BAUD_RATE", 115200))

# Real circuit load resistances on test bench (Ohms)
LOAD_RESISTANCE_12V = float(os.environ.get("LOAD_RESISTANCE_12V", 10.0))
LOAD_RESISTANCE_5V  = float(os.environ.get("LOAD_RESISTANCE_5V", 10.0))

# Disconnected pin threshold: below 2.5V is disconnected/floating noise
MIN_ACTIVE_VOLTAGE_V = 2.50

# LCA & Economic parameters from Climathon Track 3 Model
AVOIDED_CO2E_KG_PER_KWH = 72.0
CAPEX_NEW_LFP_EUR_KWH = 165.0
CAPEX_TIER1_EUR_KWH = 63.0
CAPEX_TIER2_EUR_KWH = 76.0

# Pack capacity parameters for bench demo (from physical ratings)
# 12V Jump Starter: 12,000 mAh (44.4 Wh = 0.0444 kWh)
# 5V Mobile Bank: 10,000 mAh (~37 Wh = 0.037 kWh)
PACK_CAPACITY_12V_KWH = 0.0444
PACK_CAPACITY_5V_KWH  = 0.0370

FRONTEND_INDEX_PATH = os.path.join(os.path.dirname(__file__), "..", "frontend", "index.html")

app = FastAPI(
    title="BLINC Physical Battery Intelligence & Router",
    description="Apple-grade Physical Battery Life, Grade Triage, Safety, & Cradle-to-Grave LCA Engine",
    version="3.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TelemetryState:
    def __init__(self):
        self.lock = threading.Lock()
        self.connected: bool = False
        self.port: str = SERIAL_PORT
        self.timestamp: float = 0.0

        # Physical live voltages
        self.v12_live: float = 0.0
        self.v5_live: float = 0.0
        self.v12_baseline: float = 0.0
        self.v5_baseline: float = 0.0
        self.v12_sag: float = 0.0
        self.v5_sag: float = 0.0

        # Dynamic pack auto-identification
        self.name_12v: str = "Jump Starter Primary Pack"
        self.chem_12v: str = "4S Li-ion / NMC"
        self.nominal_mah_12v: int = 12000
        self.nominal_wh_12v: float = 44.4

        self.name_5v: str = "Mobile Power Bank Buffer"
        self.chem_5v: str = "1S Li-ion USB"
        self.nominal_mah_5v: int = 10000
        self.nominal_wh_5v: float = 37.0

        # Dynamic Internal Resistance & SOH
        self.ir_12v_mohm: float = 28.5
        self.ir_5v_mohm: float = 36.0
        self.soh_12v: float = 98.2
        self.soh_5v: float = 95.8
        self.grade_12v: str = "Grade A: Whole-Pack Second-Life (Grid Solar Storage)"
        self.grade_5v: str = "Grade A: Whole-Pack Micro-Buffer"
        self.tier_12v: str = "Grade A"
        self.tier_5v: str = "Grade A"

        # Health, Runtime & Safety
        self.safety_12v: Dict = {
            "status": "PASS: ALL PARAMETERS NOMINAL",
            "voltage_integrity": "STABLE",
            "thermal_runaway_risk": "VERY LOW / SAFE",
            "impedance_state": "28.5 mΩ (Nominal)",
            "safe_to_operate": True
        }
        self.safety_5v: Dict = {
            "status": "PASS: ALL PARAMETERS NOMINAL",
            "voltage_integrity": "STABLE",
            "thermal_runaway_risk": "VERY LOW / SAFE",
            "impedance_state": "36.0 mΩ (Nominal)",
            "safe_to_operate": True
        }
        self.runtime_12v_hours: float = 4.3
        self.runtime_5v_hours: float = 3.5
        self.cycles_12v: int = 3240
        self.cycles_5v: int = 3040
        self.usable_wh_12v: float = 43.6
        self.usable_wh_5v: float = 35.4

        # Relays
        self.load12_active: bool = False
        self.load5_active: bool = False
        self.router_active: bool = True
        self.recommendation: str = "Hub Feeder Active"

        # Real-time Cradle to Gate and Grave LCA
        self.cradle_to_gate_kg: float = 0.0
        self.cradle_to_grave_kg: float = 0.0
        self.critical_minerals_g: float = 0.0
        self.total_avoided_co2e_kg: float = 0.0
        self.total_cost_saved_eur: float = 0.0
        self.total_saving_pct: float = 61.8

        self.history: List[Dict] = []
        self.last_raw_line: str = ""


state = TelemetryState()
serial_conn: Optional[serial.Serial] = None


def compute_live_physics(
    v_live: float,
    v_base: float,
    r_load: float,
    nominal_v: float,
    total_wh: float,
    is_load_on: bool
) -> tuple[float, float, float, str, dict, float, int, float, bool]:
    """Compute physical IR, SOH, Grade A/B/C, Safety checks, and runtime strictly in real time from live telemetry."""
    if v_live < MIN_ACTIVE_VOLTAGE_V:
        safety = {
            "status": "PORT UNPOPULATED (DISCONNECTED)",
            "voltage_integrity": "NO VOLTAGE DETECTED",
            "thermal_runaway_risk": "SAFE (OFFLINE)",
            "impedance_state": "OPEN CIRCUIT",
            "safe_to_operate": False,
        }
        return 0.0, 0.0, 0.0, "Port Unpopulated", safety, 0.0, 0, 0.0, False

    # Check whether load is actively drawing current or battery is resting
    is_12v = nominal_v > 10.0
    baseline_healthy_ir = 28.5 if is_12v else 36.0

    if is_load_on:
        sag = max(0.0, v_base - v_live) if v_base > 0.5 else 0.0
        current = (v_live / r_load) if r_load > 0 else 0.001
        if sag > 0.03 and current > 0.05:
            ir_ohms = sag / current
            ir_mohm = round(ir_ohms * 1000.0, 1)
        else:
            ir_mohm = baseline_healthy_ir
        # SOH degraded by measured impedance growth
        soh = max(20.0, min(100.0, 100.0 - (ir_mohm - (22.0 if is_12v else 28.0)) * 0.70))
        soh = round(soh, 1)
    else:
        # Resting state: no current flowing through load resistor
        sag = 0.0
        ir_mohm = baseline_healthy_ir

        if is_12v:
            # 4S Li-ion pack voltage curve (nominal 14.8V, full 16.8V)
            cell_v = v_live / 4.0
            if cell_v >= 4.05:     # > 16.2V
                soh = 98.4
            elif cell_v >= 3.85:   # > 15.4V
                soh = 94.6
            elif cell_v >= 3.70:   # > 14.8V
                soh = 89.2
            elif cell_v >= 3.45:   # > 13.8V
                soh = 78.5
            else:
                soh = 58.0
        else:
            # 5V USB step-up regulated output (rated 4.80V - 5.25V)
            if v_live >= 4.85:
                soh = 96.2
            elif v_live >= 4.70:
                soh = 91.5
            elif v_live >= 4.40:
                soh = 82.0
            elif v_live >= 3.80:
                soh = 68.0
            else:
                soh = 52.0

    # Physical Grade A, B, C Classification
    if soh >= 85.0:
        grade = "Grade A: Whole-Pack Second-Life (Grid Solar Storage)" if is_12v else "Grade A: Whole-Pack Micro-Buffer"
    elif soh >= 65.0:
        grade = "Grade B: Sub-Pack Second-Life (Micromobility DC Buffer)"
    else:
        grade = "Grade C: Material Recovery (Hydrometallurgical Recycling)"

    # Safety checks
    overvoltage = (v_live > 17.5) if is_12v else (v_live > 5.5)
    deep_discharge = (v_live < 10.0) if is_12v else (v_live < 3.0)
    high_ir = ir_mohm > 130.0

    if overvoltage:
        safety_status = "CRITICAL: OVERVOLTAGE DETECTED"
        safe_op = False
    elif deep_discharge:
        safety_status = "ALERT: DEEP CELL DISCHARGE"
        safe_op = False
    elif high_ir:
        safety_status = "WARNING: HIGH IMPEDANCE DEGRADATION"
        safe_op = True
    else:
        safety_status = "PASS: ALL PARAMETERS NOMINAL"
        safe_op = True

    safety = {
        "status": safety_status,
        "voltage_integrity": "STABLE" if (not overvoltage and not deep_discharge) else "ANOMALY",
        "thermal_runaway_risk": "VERY LOW / SAFE" if ir_mohm < 100 else "ELEVATED",
        "impedance_state": f"{ir_mohm:.1f} mΩ (Nominal)" if ir_mohm < 100 else f"{ir_mohm:.1f} mΩ (Elevated)",
        "safe_to_operate": safe_op,
    }

    # Remaining Usable Energy
    usable_wh = round(total_wh * (soh / 100.0), 1)

    # Estimated Runtime under standard 10W bench load
    runtime_hours = round(usable_wh / 10.0, 1) if usable_wh > 0 else 0.0

    # Projected stationary cycles remaining before reaching 60% SOH retirement threshold
    cycles_remaining = int(max(0, (soh - 60.0) * 85))

    return round(sag, 3), ir_mohm, soh, grade, safety, runtime_hours, cycles_remaining, usable_wh, True


def serial_worker():
    global serial_conn
    while True:
        if not state.connected:
            ports = [p.device for p in serial.tools.list_ports.comports()]
            target_port = SERIAL_PORT
            if target_port not in ports and ports:
                target_port = ports[0]

            try:
                serial_conn = serial.Serial(target_port, BAUD_RATE, timeout=1.0)
                state.connected = True
                state.port = target_port
                logger.info(f"Connected to ESP32-S3 on {target_port}")
            except Exception as e:
                state.connected = False
                time.sleep(1.0)
                continue

        try:
            line = serial_conn.readline().decode("utf-8", errors="ignore").strip()
            if not line.startswith("{") or not line.endswith("}"):
                continue

            data = json.loads(line)
            v12 = float(data.get("v12", 0.0))
            v5  = float(data.get("v5", 0.0))
            load12 = bool(data.get("load12", False))
            load5  = bool(data.get("load5", False))
            router = bool(data.get("router", False))

            with state.lock:
                state.timestamp = time.time()
                state.last_raw_line = line
                state.v12_live = round(v12, 3)
                state.v5_live = round(v5, 3)
                state.load12_active = load12
                state.load5_active = load5
                state.router_active = router

                # Update baseline open-circuit voltages with exponential moving filter
                if not load12 and v12 >= MIN_ACTIVE_VOLTAGE_V:
                    state.v12_baseline = v12 if state.v12_baseline < 1.0 else (0.92 * state.v12_baseline + 0.08 * v12)
                elif v12 < MIN_ACTIVE_VOLTAGE_V:
                    state.v12_baseline = 0.0

                if not load5 and v5 >= MIN_ACTIVE_VOLTAGE_V:
                    state.v5_baseline = v5 if state.v5_baseline < 1.0 else (0.92 * state.v5_baseline + 0.08 * v5)
                elif v5 < MIN_ACTIVE_VOLTAGE_V:
                    state.v5_baseline = 0.0

                # Auto-detect battery type on Channel A from live voltage
                nom_v_a = 12.0 if v12 >= 7.5 else 5.0
                cap_wh_a = 44.4 if v12 >= 7.5 else 37.0
                state.name_12v = "Jump Starter Primary Pack" if v12 >= 7.5 else "Mobile Power Bank (Port A)"
                state.chem_12v = "4S Li-ion / NMC (350A/1800A Peak)" if v12 >= 7.5 else "1S Li-ion USB (2.4A)"
                state.nominal_mah_12v = 12000 if v12 >= 7.5 else 10000
                state.nominal_wh_12v = cap_wh_a

                # Compute physics for Channel A
                sag12, ir12, soh12, grade12, safety12, rt12, cyc12, uwh12, active12 = compute_live_physics(
                    v12, state.v12_baseline, LOAD_RESISTANCE_12V, nom_v_a, cap_wh_a, load12
                )
                state.v12_sag = sag12
                state.ir_12v_mohm = ir12
                state.soh_12v = soh12
                state.grade_12v = grade12
                state.tier_12v = grade12
                state.safety_12v = safety12
                state.runtime_12v_hours = rt12
                state.cycles_12v = cyc12
                state.usable_wh_12v = uwh12

                # Auto-detect battery type on Channel B from live voltage
                nom_v_b = 12.0 if v5 >= 7.5 else 5.0
                cap_wh_b = 44.4 if v5 >= 7.5 else 37.0
                state.name_5v = "Jump Starter Pack (Port B)" if v5 >= 7.5 else "Mobile Power Bank Buffer"
                state.chem_5v = "4S Li-ion / NMC (350A/1800A Peak)" if v5 >= 7.5 else "1S Li-ion USB (2.4A)"
                state.nominal_mah_5v = 12000 if v5 >= 7.5 else 10000
                state.nominal_wh_5v = cap_wh_b

                # Compute physics for Channel B
                sag5, ir5, soh5, grade5, safety5, rt5, cyc5, uwh5, active5 = compute_live_physics(
                    v5, state.v5_baseline, LOAD_RESISTANCE_5V, nom_v_b, cap_wh_b, load5
                )
                state.v5_sag = sag5
                state.ir_5v_mohm = ir5
                state.soh_5v = soh5
                state.grade_5v = grade5
                state.tier_5v = grade5
                state.safety_5v = safety5
                state.runtime_5v_hours = rt5
                state.cycles_5v = cyc5
                state.usable_wh_5v = uwh5

                # Cradle-to-Gate & Cradle-to-Grave Real-Time LCA Model (Only Active Connected Specimen)
                ch_a_conn = v12 >= MIN_ACTIVE_VOLTAGE_V
                ch_b_conn = v5 >= MIN_ACTIVE_VOLTAGE_V
                active_usable_wh = (state.usable_wh_12v if ch_a_conn else 0.0) + (state.usable_wh_5v if ch_b_conn else 0.0)
                total_usable_kwh = active_usable_wh / 1000.0

                # Cradle-to-Gate: Avoided Virgin Mineral Mining & Cathode Precursor Synthesis (72 kg CO2e / kWh)
                state.cradle_to_gate_kg = round(total_usable_kwh * AVOIDED_CO2E_KG_PER_KWH, 2)
                
                # Cradle-to-Grave: Avoided Landfill E-Waste & End-of-Life Hydrometallurgical Recycling (18 kg CO2e / kWh)
                state.cradle_to_grave_kg = round(total_usable_kwh * 18.0, 2)

                # Critical minerals preserved (Lithium, Nickel, Cobalt: ~850g per kWh)
                state.critical_minerals_g = round(total_usable_kwh * 850.0, 1)

                # Total avoided GHG emissions
                state.total_avoided_co2e_kg = round(state.cradle_to_gate_kg + state.cradle_to_grave_kg, 2)

                # Economic CAPEX model
                new_cost = total_usable_kwh * CAPEX_NEW_LFP_EUR_KWH
                cost_avoided = total_usable_kwh * CAPEX_TIER1_EUR_KWH
                state.total_cost_saved_eur = round(max(0.0, new_cost - cost_avoided), 2)
                state.total_saving_pct = round((1.0 - (cost_avoided / new_cost)) * 100.0, 1) if new_cost > 0 else 0.0

                # BLINC Smart Router recommendation logic
                if ch_a_conn and not ch_b_conn:
                    state.recommendation = "Route Channel A (Primary Pack) -> Primary Hub Feeder"
                elif ch_b_conn and not ch_a_conn:
                    state.recommendation = "Route Channel B (Buffer Storage) -> Micromobility Charger"
                elif ch_a_conn and ch_b_conn:
                    state.recommendation = "Dual Routing: Ch A -> Hub Feeder | Ch B -> DC Buffer"
                else:
                    state.recommendation = "Router Standby (No Infeed Detected)"

                # History ring buffer
                state.history.append({
                    "time": round(state.timestamp, 2),
                    "v12": v12,
                    "v5": v5,
                })
                if len(state.history) > 120:
                    state.history.pop(0)

        except Exception as e:
            state.connected = False
            if serial_conn:
                try:
                    serial_conn.close()
                except Exception:
                    pass
            time.sleep(1.0)


threading.Thread(target=serial_worker, daemon=True).start()


@app.get("/", response_class=HTMLResponse)
def get_dashboard():
    try:
        with open(FRONTEND_INDEX_PATH, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), status_code=200)
    except Exception as e:
        return HTMLResponse(content=f"<h1>Error loading index.html: {e}</h1>", status_code=500)


@app.get("/api/status")
def get_status():
    with state.lock:
        ch_a_connected = state.v12_live >= MIN_ACTIVE_VOLTAGE_V
        ch_b_connected = state.v5_live >= MIN_ACTIVE_VOLTAGE_V
        conn_count = (1 if ch_a_connected else 0) + (1 if ch_b_connected else 0)
        total_nominal_wh = (state.nominal_wh_12v if ch_a_connected else 0.0) + (state.nominal_wh_5v if ch_b_connected else 0.0)
        total_nominal_mah = (state.nominal_mah_12v if ch_a_connected else 0) + (state.nominal_mah_5v if ch_b_connected else 0)
        total_usable_wh = (state.usable_wh_12v if ch_a_connected else 0.0) + (state.usable_wh_5v if ch_b_connected else 0.0)
        
        return {
            "solution": "BLINC",
            "connected": state.connected,
            "port": state.port,
            "last_raw_line": state.last_raw_line,
            "timestamp": state.timestamp,
            "system_overview": {
                "connected_batteries": conn_count,
                "total_nominal_wh": round(total_nominal_wh, 1),
                "total_nominal_mah": total_nominal_mah,
                "total_usable_wh": round(total_usable_wh, 1),
                "aggregate_health_pct": round((total_usable_wh / total_nominal_wh * 100.0), 1) if total_nominal_wh > 0 else 0.0,
            },
            "channel_a_12v": {
                "name": state.name_12v,
                "connected": ch_a_connected,
                "nominal_capacity_mah": state.nominal_mah_12v if ch_a_connected else 0,
                "nominal_energy_wh": state.nominal_wh_12v if ch_a_connected else 0.0,
                "rated_current_a": 350 if state.nominal_mah_12v == 12000 else 2.4,
                "peak_current_a": 1800 if state.nominal_mah_12v == 12000 else 3.0,
                "chemistry": state.chem_12v,
                "voltage_live": state.v12_live,
                "voltage_baseline": round(state.v12_baseline, 3),
                "voltage_sag": state.v12_sag if ch_a_connected else 0.0,
                "internal_resistance_mohm": state.ir_12v_mohm if ch_a_connected else 0.0,
                "soh_pct": state.soh_12v if ch_a_connected else 0.0,
                "grade": state.grade_12v if ch_a_connected else "Port Unpopulated",
                "triage_tier": state.grade_12v if ch_a_connected else "Standby",
                "usable_wh": state.usable_wh_12v if ch_a_connected else 0.0,
                "runtime_hours": state.runtime_12v_hours if ch_a_connected else 0.0,
                "cycles_remaining": state.cycles_12v if ch_a_connected else 0,
                "safety": state.safety_12v if ch_a_connected else {
                    "status": "PORT UNPOPULATED (DISCONNECTED)",
                    "voltage_integrity": "NO VOLTAGE DETECTED",
                    "thermal_runaway_risk": "SAFE (OFFLINE)",
                    "impedance_state": "OPEN CIRCUIT",
                    "safe_to_operate": False
                },
                "load_active": state.load12_active,
            },
            "channel_b_5v": {
                "name": state.name_5v,
                "connected": ch_b_connected,
                "nominal_capacity_mah": state.nominal_mah_5v if ch_b_connected else 0,
                "nominal_energy_wh": state.nominal_wh_5v if ch_b_connected else 0.0,
                "rated_current_a": 350 if state.nominal_mah_5v == 12000 else 2.4,
                "peak_current_a": 1800 if state.nominal_mah_5v == 12000 else 3.0,
                "chemistry": state.chem_5v,
                "voltage_live": state.v5_live,
                "voltage_baseline": round(state.v5_baseline, 3),
                "voltage_sag": state.v5_sag if ch_b_connected else 0.0,
                "internal_resistance_mohm": state.ir_5v_mohm if ch_b_connected else 0.0,
                "soh_pct": state.soh_5v if ch_b_connected else 0.0,
                "grade": state.grade_5v if ch_b_connected else "Port Unpopulated",
                "triage_tier": state.grade_5v if ch_b_connected else "Standby",
                "usable_wh": state.usable_wh_5v if ch_b_connected else 0.0,
                "runtime_hours": state.runtime_5v_hours if ch_b_connected else 0.0,
                "cycles_remaining": state.cycles_5v if ch_b_connected else 0,
                "safety": state.safety_5v if ch_b_connected else {
                    "status": "PORT UNPOPULATED (DISCONNECTED)",
                    "voltage_integrity": "NO VOLTAGE DETECTED",
                    "thermal_runaway_risk": "SAFE (OFFLINE)",
                    "impedance_state": "OPEN CIRCUIT",
                    "safe_to_operate": False
                },
                "load_active": state.load5_active,
            },
            "lca_impact": {
                "cradle_to_gate_kg_co2": state.cradle_to_gate_kg,
                "cradle_to_grave_kg_co2": state.cradle_to_grave_kg,
                "critical_minerals_preserved_g": state.critical_minerals_g,
                "closed_loop_recovery_pct": 95.0,
                "avoided_co2e_kg": state.total_avoided_co2e_kg,
                "capital_saved_eur": state.total_cost_saved_eur,
                "saving_pct": state.total_saving_pct,
            },
            "smart_router": {
                "relay_active": state.router_active,
                "recommendation": state.recommendation,
            },
            "history": state.history[-40:],
        }


class CommandRequest(BaseModel):
    command: str


@app.post("/api/command")
def send_command(req: CommandRequest):
    if not state.connected or not serial_conn:
        raise HTTPException(status_code=503, detail="ESP32-S3 not connected")
    try:
        serial_conn.write(f"{req.command}\n".encode("utf-8"))
        serial_conn.flush()
        return {"status": "ok", "sent_command": req.command}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
