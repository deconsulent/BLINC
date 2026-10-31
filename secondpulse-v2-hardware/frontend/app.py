# frontend/app.py
# SecondPulse V2: Zero-Simulation Physical Hardware Dashboard
# Displays real-time voltage, voltage sag, internal resistance, and active relay router controls.

import os
import time
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

DAEMON_URL = os.environ.get("DAEMON_URL", "http://localhost:8000")

st.set_page_config(
    page_title="SecondPulse V2 | Physical Hardware Triage",
    page_icon="⚡",
    layout="wide",
)

st.title("⚡ SecondPulse V2: Physical Battery Triage & Smart Router")
st.caption("Climathon 2026 Track 3 — Zero-Simulation Edge Hardware System (Raspberry Pi 4B + Arduino Micro)")


def fetch_telemetry():
    try:
        r = requests.get(f"{DAEMON_URL}/api/status", timeout=1.0)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def send_hardware_command(cmd: str):
    try:
        r = requests.post(f"{DAEMON_URL}/api/command", json={"command": cmd}, timeout=2.0)
        return r.status_code == 200
    except Exception as e:
        st.error(f"Command failed: {e}")
        return False


data = fetch_telemetry()

# Top Hardware Connection Status Bar
status_col1, status_col2, status_col3 = st.columns([1, 1, 2])

with status_col1:
    if data and data.get("connected"):
        st.success(f"🟢 Arduino Micro Online ({data.get('port')})")
    else:
        st.warning("🟡 Awaiting Serial Port (/dev/ttyACM0)")

with status_col2:
    if data and data.get("smart_router", {}).get("relay_active"):
        st.info("🔌 Router Relay: ACTIVE (Output Powered)")
    else:
        st.secondary("⚪ Router Relay: IDLE")

with status_col3:
    if data:
        rec = data.get("smart_router", {}).get("recommendation", "")
        st.info(f"💡 Strategy: {rec}")

st.markdown("---")

# Main Split: Channel A (12V Car Jump Starter) vs Channel B (5V Mobile Bank)
col_a, col_b = st.columns(2)

with col_a:
    st.subheader("🔋 Channel A: 12V Car Jump Starter")
    ch_a = data.get("channel_a_12v", {}) if data else {}
    v12 = ch_a.get("voltage_live", 0.0)
    voc12 = ch_a.get("voltage_open_circuit", 12.6)
    sag12 = ch_a.get("voltage_sag", 0.0)
    ir12 = ch_a.get("internal_resistance_mohm", 0.0)
    soh12 = ch_a.get("soh_pct", 100.0)
    tier12 = ch_a.get("triage_tier", "Awaiting Test")

    m1, m2, m3 = st.columns(3)
    m1.metric("Live Voltage", f"{v12:.2f} V", delta=f"-{sag12:.2f} V sag" if sag12 > 0 else "0.0 V")
    m2.metric("Internal Resistance", f"{ir12:.1f} mΩ")
    m3.metric("State of Health", f"{soh12:.1f} %")

    st.markdown(f"**Classification:** `{tier12}`")

    st.markdown("##### Active Physical Load Control")
    btn_col1, btn_col2 = st.columns(2)
    if btn_col1.button("⚡ Pulse Load 1s (Channel A)", use_container_width=True):
        if send_hardware_command("LOAD12V_PULSE"):
            st.toast("12V 1-Second Load Pulse Activated!")
    if btn_col2.button("Toggle Continuous Load", use_container_width=True):
        cmd = "LOAD12V_OFF" if ch_a.get("load_active") else "LOAD12V_ON"
        send_hardware_command(cmd)

with col_b:
    st.subheader("🔋 Channel B: 5V Mobile Power Bank")
    ch_b = data.get("channel_b_5v", {}) if data else {}
    v5 = ch_b.get("voltage_live", 0.0)
    voc5 = ch_b.get("voltage_open_circuit", 5.0)
    sag5 = ch_b.get("voltage_sag", 0.0)
    ir5 = ch_b.get("internal_resistance_mohm", 0.0)
    soh5 = ch_b.get("soh_pct", 100.0)
    tier5 = ch_b.get("triage_tier", "Awaiting Test")

    m4, m5, m6 = st.columns(3)
    m4.metric("Live Voltage", f"{v5:.2f} V", delta=f"-{sag5:.2f} V sag" if sag5 > 0 else "0.0 V")
    m5.metric("Internal Resistance", f"{ir5:.1f} mΩ")
    m6.metric("State of Health", f"{soh5:.1f} %")

    st.markdown(f"**Classification:** `{tier5}`")

    st.markdown("##### Active Physical Load Control")
    btn_col3, btn_col4 = st.columns(2)
    if btn_col3.button("⚡ Pulse Load 1s (Channel B)", use_container_width=True):
        if send_hardware_command("LOAD5V_PULSE"):
            st.toast("5V 1-Second Load Pulse Activated!")
    if btn_col4.button("Toggle Continuous Load (B)", use_container_width=True):
        cmd = "LOAD5V_OFF" if ch_b.get("load_active") else "LOAD5V_ON"
        send_hardware_command(cmd)

st.markdown("---")

# Smart Router Section
st.subheader("🔀 Autonomous Power Router to Micromobility Hub")
r_col1, r_col2 = st.columns([2, 1])

with r_col1:
    st.write(
        "The smart router continuously compares the real internal resistance and health of both physical packs. "
        "Upon triggering, Relay 3 connects the healthiest pack to the solar hub output load."
    )

with r_col2:
    if st.button("🚀 Switch Router Relay (Output Feed)", use_container_width=True):
        current_state = data.get("smart_router", {}).get("relay_active", False) if data else False
        new_cmd = "ROUTE_OFF" if current_state else "ROUTE_ON"
        send_hardware_command(new_cmd)

# Real-Time Telemetry Graph
st.markdown("---")
st.subheader("📈 Real-Time Physical Voltage Strip Chart")

if data and "history" in data and len(data["history"]) > 0:
    df = pd.DataFrame(data["history"])
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df["time"], y=df["v12"], mode="lines", name="12V Car Jump Starter (V)"))
    fig.add_trace(go.Scatter(x=df["time"], y=df["v5"], mode="lines", name="5V Mobile Power Bank (V)"))
    fig.update_layout(
        xaxis_title="Epoch Seconds",
        yaxis_title="Voltage (V)",
        height=350,
        margin=dict(l=20, r=20, t=20, b=20),
    )
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("Streaming live hardware data... Run hardware_daemon.py with Arduino Micro connected.")

# Periodic live refresh
time.sleep(0.5)
st.rerun()
