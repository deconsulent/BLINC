# backend/hardware_daemon.py
# SecondPulse V2: Physical Serial Daemon and Battery Physics Health Engine.
# Connects to Arduino Micro via USB serial, performs live active load sag analysis,
# calculates real internal resistance (IR), SOH and triage classification.

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
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("HardwareDaemon")

SERIAL_PORT = os.environ.get("SERIAL_PORT", "/dev/ttyACM0")
BAUD_RATE = int(os.environ.get("BAUD_RATE", 115200))

# Fallback test mode if no physical serial port is attached
DEMO_MODE = os.environ.get("DEMO_MODE", "0") == "1"

# Hardware circuit load parameters for IR calculation
# Adjust based on bench load resistors
LOAD_RESISTANCE_12V = float(os.environ.get("LOAD_RESISTANCE_12V", 10.0)) # 10 Ohm dummy load
LOAD_RESISTANCE_5V  = float(os.environ.get("LOAD_RESISTANCE_5V", 10.0))  # 10 Ohm dummy load

app = FastAPI(
    title="SecondPulse V2 Physical Hardware Daemon",
    description="Live ADC telemetry, active IR characterization, and battery triage server.",
    version="2.0.0",
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
        self.v12_live: float = 0.0
        self.v5_live: float = 0.0
        self.load12_active: bool = False
        self.load5_active: bool = False
        self.router_active: bool = False

        # Physics baseline and pulse history for IR calculation
        self.v12_open_circuit: float = 12.60
        self.v5_open_circuit: float = 5.05
        self.v12_sag: float = 0.0
        self.v5_sag: float = 0.0
        self.ir_12v_mohm: float = 0.0
        self.ir_5v_mohm: float = 0.0

        # Health & Triage evaluations
        self.soh_12v: float = 100.0
        self.soh_5v: float = 100.0
        self.tier_12v: str = "Evaluating"
        self.tier_5v: str = "Evaluating"
        self.recommendation: str = "Pending pulse test"

        self.history: List[Dict] = []


state = TelemetryState()
serial_conn: Optional[serial.Serial] = None


def calculate_physics(v_oc: float, v_load: float, r_load: float) -> tuple[float, float, float]:
    """Calculate internal resistance and estimated health based on voltage sag under load."""
    if v_oc <= 0.5 or v_load <= 0.5:
        return 0.0, 0.0, 0.0

    delta_v = max(0.0, v_oc - v_load)
    current = v_load / r_load if r_load > 0 else 0.001
    ir_ohms = delta_v / current if current > 0 else 0.0
    ir_mohm = round(ir_ohms * 1000.0, 1)

    # Health estimation based on internal resistance degradation
    # A fresh 12V AGM/Lithium jump starter has IR between 15-50 mOhm
    # Degraded pack: > 150 mOhm
    if ir_mohm < 60.0:
        soh = max(85.0, 100.0 - (ir_mohm / 60.0) * 15.0)
    elif ir_mohm < 180.0:
        soh = max(65.0, 85.0 - ((ir_mohm - 60.0) / 120.0) * 20.0)
    else:
        soh = max(30.0, 65.0 - ((ir_mohm - 180.0) / 200.0) * 35.0)

    return delta_v, ir_mohm, round(soh, 1)


def triage_pack(soh: float, v_oc: float, expected_nominal: float) -> str:
    """Triage tier assignment per Climathon criteria."""
    if v_oc < (expected_nominal * 0.70):
        return "Tier 3: Damaged / Deep Discharge (Direct Recycling)"
    if soh >= 75.0:
        return "Tier 1: High Health Whole-Pack Second Life (Solar Storage Hub)"
    if soh >= 60.0:
        return "Tier 2: Intermediate Health (Low-Demand Micromobility Buffer)"
    return "Tier 3: End of Life (Material Recycling)"


def serial_worker():
    global serial_conn
    while True:
        # Check available ports or reconnect
        if not state.connected:
            ports = [p.device for p in serial.tools.list_ports.comports()]
            target_port = SERIAL_PORT
            if target_port not in ports and ports:
                # Auto select ACM or USB port if default not found
                target_port = ports[0]

            try:
                serial_conn = serial.Serial(target_port, BAUD_RATE, timeout=1.0)
                state.connected = True
                state.port = target_port
                logger.info(f"Connected to Arduino Micro on {target_port}")
            except Exception as e:
                state.connected = False
                time.sleep(2.0)
                continue

        try:
            line = serial_conn.readline().decode("utf-8", errors="ignore").strip()
            if not line.startswith("{") or not line.endswith("}"):
                continue

            data = json.loads(line)
            v12 = float(data.get("v12", 0.0))
            v5 = float(data.get("v5", 0.0))
            load12 = bool(data.get("load12", False))
            load5 = bool(data.get("load5", False))
            router = bool(data.get("router", False))

            with state.lock:
                state.timestamp = time.time()
                state.v12_live = v12
                state.v5_live = v5
                state.load12_active = load12
                state.load5_active = load5
                state.router_active = router

                # Track Open Circuit Voltage when load is off
                if not load12 and v12 > 1.0:
                    state.v12_open_circuit = 0.95 * state.v12_open_circuit + 0.05 * v12
                if not load5 and v5 > 1.0:
                    state.v5_open_circuit = 0.95 * state.v5_open_circuit + 0.05 * v5

                # Calculate IR during load active condition
                if load12 and v12 > 0.5:
                    sag, ir, soh = calculate_physics(state.v12_open_circuit, v12, LOAD_RESISTANCE_12V)
                    state.v12_sag = round(sag, 3)
                    state.ir_12v_mohm = ir
                    state.soh_12v = soh
                    state.tier_12v = triage_pack(soh, state.v12_open_circuit, 12.0)

                if load5 and v5 > 0.5:
                    sag, ir, soh = calculate_physics(state.v5_open_circuit, v5, LOAD_RESISTANCE_5V)
                    state.v5_sag = round(sag, 3)
                    state.ir_5v_mohm = ir
                    state.soh_5v = soh
                    state.tier_5v = triage_pack(soh, state.v5_open_circuit, 5.0)

                # Determine active routing recommendation
                if state.soh_12v >= state.soh_5v:
                    state.recommendation = "Route Channel A (12V Pack) to Secondary Storage Hub"
                else:
                    state.recommendation = "Route Channel B (5V Pack) to Secondary Storage Hub"

                # Store history buffer
                state.history.append({
                    "time": round(state.timestamp, 2),
                    "v12": v12,
                    "v5": v5,
                    "load12": load12,
                    "load5": load5,
                })
                if len(state.history) > 200:
                    state.history.pop(0)

        except Exception as e:
            logger.warning(f"Serial communication error: {e}")
            state.connected = False
            if serial_conn:
                try:
                    serial_conn.close()
                except Exception:
                    pass
            time.sleep(1.0)


# Start daemon background thread
threading.Thread(target=serial_worker, daemon=True).start()


@app.get("/api/status")
def get_status():
    """Return live voltage, load states, and internal resistance analytics."""
    with state.lock:
        return {
            "connected": state.connected,
            "port": state.port,
            "timestamp": state.timestamp,
            "channel_a_12v": {
                "voltage_live": state.v12_live,
                "voltage_open_circuit": round(state.v12_open_circuit, 3),
                "voltage_sag": state.v12_sag,
                "load_active": state.load12_active,
                "internal_resistance_mohm": state.ir_12v_mohm,
                "soh_pct": state.soh_12v,
                "triage_tier": state.tier_12v,
            },
            "channel_b_5v": {
                "voltage_live": state.v5_live,
                "voltage_open_circuit": round(state.v5_open_circuit, 3),
                "voltage_sag": state.v5_sag,
                "load_active": state.load5_active,
                "internal_resistance_mohm": state.ir_5v_mohm,
                "soh_pct": state.soh_5v,
                "triage_tier": state.tier_5v,
            },
            "smart_router": {
                "relay_active": state.router_active,
                "recommendation": state.recommendation,
            },
            "history": state.history[-50:],
        }


class CommandRequest(BaseModel):
    command: str


@app.post("/api/command")
def send_command(req: CommandRequest):
    """Send load or router switching command to the Arduino Micro."""
    valid_commands = {
        "LOAD12V_PULSE", "LOAD5V_PULSE",
        "LOAD12V_ON", "LOAD12V_OFF",
        "LOAD5V_ON", "LOAD5V_OFF",
        "ROUTE_ON", "ROUTE_OFF"
    }
    if req.command not in valid_commands:
        raise HTTPException(status_code=400, detail="Invalid hardware command")

    if not state.connected or not serial_conn:
        raise HTTPException(status_code=503, detail="Arduino Micro hardware not connected")

    try:
        serial_conn.write(f"{req.command}\n".encode("utf-8"))
        serial_conn.flush()
        return {"status": "ok", "sent_command": req.command}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to transmit command: {e}")
