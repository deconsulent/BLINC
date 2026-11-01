# frontend/app.py
# BLINC: Battery Life & Intelligence Node Controller
# Apple-Inspired Single-Screen Industrial Battery Triage, Circularity & Router Dashboard
# Clean, responsive viewport optimized for Emil Kowalski Apple design principles.

import os
import time
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

DAEMON_URL = os.environ.get("DAEMON_URL", "http://localhost:8000")

st.set_page_config(
    page_title="BLINC | Battery Life & Intelligence Node Controller",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Apple Design System: Dark Glassmorphism, SF Pro hierarchy, zero-clutter single screen
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

    html, body, [class*="css"] {
        font-family: -apple-system, BlinkMacSystemFont, "Inter", "SF Pro Display", "SF Pro Text", sans-serif;
        background-color: #07080a;
        color: #f5f5f7;
    }
    
    /* Remove default Streamlit top padding to fit one screen */
    .block-container {
        padding-top: 1.2rem !important;
        padding-bottom: 1.2rem !important;
        padding-left: 2rem !important;
        padding-right: 2rem !important;
        max-width: 100% !important;
    }

    header[data-testid="stHeader"] {
        background: transparent !important;
    }
    
    /* Apple translucent card component */
    .apple-card {
        background: rgba(255, 255, 255, 0.03);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 14px;
        padding: 16px 20px;
        backdrop-filter: blur(20px);
        -webkit-backdrop-filter: blur(20px);
        margin-bottom: 14px;
        transition: border 0.2s ease;
    }
    .apple-card:hover {
        border: 1px solid rgba(255, 255, 255, 0.16);
    }

    /* Metric Typography */
    .apple-label {
        font-size: 0.72rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        color: #86868b;
        margin-bottom: 4px;
    }
    .apple-val-hero {
        font-size: 1.9rem;
        font-weight: 700;
        letter-spacing: -0.02em;
        color: #ffffff;
        line-height: 1.1;
    }
    .apple-val-sub {
        font-size: 0.8rem;
        color: #86868b;
        margin-top: 4px;
    }
    
    /* Grade Badges */
    .badge-grade-a {
        display: inline-block;
        background: rgba(48, 209, 88, 0.15);
        border: 1px solid rgba(48, 209, 88, 0.35);
        color: #30d158;
        font-weight: 600;
        font-size: 0.75rem;
        padding: 4px 10px;
        border-radius: 8px;
    }
    .badge-grade-b {
        display: inline-block;
        background: rgba(255, 159, 10, 0.15);
        border: 1px solid rgba(255, 159, 10, 0.35);
        color: #ff9f0a;
        font-weight: 600;
        font-size: 0.75rem;
        padding: 4px 10px;
        border-radius: 8px;
    }
    .badge-grade-c {
        display: inline-block;
        background: rgba(255, 69, 58, 0.15);
        border: 1px solid rgba(255, 69, 58, 0.35);
        color: #ff453a;
        font-weight: 600;
        font-size: 0.75rem;
        padding: 4px 10px;
        border-radius: 8px;
    }
    .badge-neutral {
        display: inline-block;
        background: rgba(255, 255, 255, 0.07);
        border: 1px solid rgba(255, 255, 255, 0.14);
        color: #86868b;
        font-weight: 500;
        font-size: 0.75rem;
        padding: 4px 10px;
        border-radius: 8px;
    }

    /* Safety status indicator */
    .safety-pass {
        color: #30d158;
        font-weight: 600;
        font-size: 0.8rem;
    }
    .safety-alert {
        color: #ff453a;
        font-weight: 600;
        font-size: 0.8rem;
    }

    /* Apple-styled button */
    div.stButton > button {
        background: rgba(255, 255, 255, 0.08);
        border: 1px solid rgba(255, 255, 255, 0.12);
        color: #f5f5f7;
        font-weight: 500;
        font-size: 0.85rem;
        padding: 8px 18px;
        border-radius: 999px;
        transition: all 0.2s ease;
    }
    div.stButton > button:hover {
        background: rgba(255, 255, 255, 0.18);
        border-color: rgba(255, 255, 255, 0.28);
        color: #ffffff;
    }
</style>
""", unsafe_allow_html=True)


def fetch_telemetry():
    try:
        r = requests.get(f"{DAEMON_URL}/api/status", timeout=0.6)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def send_hardware_command(cmd: str):
    try:
        r = requests.post(f"{DAEMON_URL}/api/command", json={"command": cmd}, timeout=1.2)
        return r.status_code == 200
    except Exception as e:
        st.toast(f"Command error: {e}")
        return False


data = fetch_telemetry()

# =========================================================================
# HEADER ROW: BLINC BRANDING + SYSTEM STATUS + CONTINUOUS LOAD CONTROLS
# =========================================================================
h_col1, h_col2, h_col3 = st.columns([1.6, 2.0, 1.4])

with h_col1:
    st.markdown("""
        <div style="display: flex; align-items: baseline; gap: 10px;">
            <span style="font-size: 1.6rem; font-weight: 700; letter-spacing: -0.03em; color: #ffffff;">BLINC</span>
            <span style="font-size: 0.78rem; font-weight: 500; color: #86868b; letter-spacing: 0.02em;">BATTERY INTELLIGENCE & CIRCULARITY</span>
        </div>
        <div style="font-size: 0.76rem; color: #515154; margin-top: -2px;">
            FireBeetle 2 ESP32-S3 • COM18 • Edge Physical Benchmark
        </div>
    """, unsafe_allow_html=True)

with h_col2:
    ch_a = data.get("channel_a_12v", {}) if data else {}
    ch_b = data.get("channel_b_5v", {}) if data else {}
    router_active = data.get("smart_router", {}).get("relay_active", False) if data else False
    rec = data.get("smart_router", {}).get("recommendation", "Awaiting sensor sweep...") if data else "Awaiting telemetry..."
    
    st.markdown(f"""
        <div style="display: flex; gap: 14px; align-items: center; padding-top: 4px;">
            <div style="font-size: 0.78rem;">
                <span style="color: #86868b;">Router:</span> 
                <span style="color: {'#30d158' if router_active else '#86868b'}; font-weight: 600;">
                    {'● ACTIVE (ROUTING)' if router_active else '○ STANDBY'}
                </span>
            </div>
            <div style="font-size: 0.76rem; color: #a1a1a6; border-left: 1px solid rgba(255,255,255,0.1); padding-left: 12px;">
                {rec}
            </div>
        </div>
    """, unsafe_allow_html=True)

with h_col3:
    # Continuous Start / Stop Buttons
    c_btn1, c_btn2 = st.columns(2)
    load_any = ch_a.get("load_active", False) or ch_b.get("load_active", False)
    with c_btn1:
        if st.button("▶ Start Load", use_container_width=True):
            send_hardware_command("LOAD12V_ON")
            send_hardware_command("LOAD5V_ON")
            st.toast("Continuous Load: Activated")
    with c_btn2:
        if st.button("⏹ Stop Load", use_container_width=True):
            send_hardware_command("LOAD12V_OFF")
            send_hardware_command("LOAD5V_OFF")
            st.toast("Continuous Load: Deactivated")

st.markdown("<div style='height: 8px;'></div>", unsafe_allow_html=True)

# =========================================================================
# SYSTEM CAPACITY & CRADLE-TO-GRAVE LCA SUMMARY BAR
# =========================================================================
sys_ov = data.get("system_overview", {}) if data else {}
lca = data.get("lca_impact", {}) if data else {}

bar1, bar2, bar3, bar4, bar5 = st.columns(5)

with bar1:
    st.markdown(f"""
        <div class="apple-card">
            <div class="apple-label">Total Connected Capacity</div>
            <div class="apple-val-hero">{sys_ov.get('total_nominal_mah', 0):,} <span style="font-size: 1rem; color: #86868b; font-weight: 400;">mAh</span></div>
            <div class="apple-val-sub">{sys_ov.get('total_nominal_wh', 0.0):.1f} Wh Nominal • {sys_ov.get('connected_batteries', 0)} Pack(s)</div>
        </div>
    """, unsafe_allow_html=True)

with bar2:
    st.markdown(f"""
        <div class="apple-card">
            <div class="apple-label">Aggregate Remaining Health</div>
            <div class="apple-val-hero">{sys_ov.get('aggregate_health_pct', 0.0):.1f}<span style="font-size: 1.1rem; color: #30d158; font-weight: 500;">%</span></div>
            <div class="apple-val-sub">{sys_ov.get('total_usable_wh', 0.0):.1f} Wh Usable Second-Life</div>
        </div>
    """, unsafe_allow_html=True)

with bar3:
    st.markdown(f"""
        <div class="apple-card">
            <div class="apple-label">Cradle-to-Gate (Mining & Synthesis)</div>
            <div class="apple-val-hero">{lca.get('cradle_to_gate_kg_co2', 0.0):.2f} <span style="font-size: 1rem; color: #30d158; font-weight: 400;">kg CO₂e</span></div>
            <div class="apple-val-sub">Virgin mineral extraction avoided</div>
        </div>
    """, unsafe_allow_html=True)

with bar4:
    st.markdown(f"""
        <div class="apple-card">
            <div class="apple-label">Cradle-to-Grave (E-Waste & Circularity)</div>
            <div class="apple-val-hero">{lca.get('critical_minerals_preserved_g', 0.0):.1f} <span style="font-size: 1rem; color: #0a84ff; font-weight: 400;">g</span></div>
            <div class="apple-val-sub">{lca.get('closed_loop_recovery_pct', 95.0):.0f}% critical minerals preserved</div>
        </div>
    """, unsafe_allow_html=True)

with bar5:
    st.markdown(f"""
        <div class="apple-card">
            <div class="apple-label">Net Circular Savings</div>
            <div class="apple-val-hero">€ {lca.get('capital_saved_eur', 0.0):.2f}</div>
            <div class="apple-val-sub">{lca.get('saving_pct', 0.0):.1f}% CAPEX vs new LFP pack</div>
        </div>
    """, unsafe_allow_html=True)

# =========================================================================
# DUAL CHANNEL BATTERY TRIAGE PANELS (CHANNEL A: 12V PACK | CHANNEL B: 5V BUFFER)
# =========================================================================
col_a, col_b = st.columns(2)

# Helper function to render Grade Badge
def get_grade_badge(grade_str: str) -> str:
    if "Grade A" in grade_str:
        return f'<span class="badge-grade-a">{grade_str}</span>'
    elif "Grade B" in grade_str:
        return f'<span class="badge-grade-b">{grade_str}</span>'
    elif "Grade C" in grade_str:
        return f'<span class="badge-grade-c">{grade_str}</span>'
    return f'<span class="badge-neutral">{grade_str}</span>'

# Helper for safety badge
def get_safety_badge(safety_dict: dict) -> str:
    st_val = safety_dict.get("status", "DISCONNECTED")
    if "PASS" in st_val:
        return f'<span class="safety-pass">● {st_val}</span>'
    elif "DISCONNECTED" in st_val:
        return f'<span style="color: #86868b;">○ {st_val}</span>'
    return f'<span class="safety-alert">▲ {st_val}</span>'

# CHANNEL A: 12V CAR JUMP STARTER PACK
with col_a:
    v12 = ch_a.get("voltage_live", 0.0)
    soh12 = ch_a.get("soh_pct", 0.0)
    ir12 = ch_a.get("internal_resistance_mohm", 0.0)
    sag12 = ch_a.get("voltage_sag", 0.0)
    grade12 = ch_a.get("grade", "Disconnected")
    safety12 = ch_a.get("safety", {})
    uwh12 = ch_a.get("usable_wh", 0.0)
    rt12 = ch_a.get("runtime_hours", 0.0)
    cyc12 = ch_a.get("cycles_remaining", 0)

    st.markdown(f"""
        <div class="apple-card">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                <div>
                    <span style="font-size: 1.05rem; font-weight: 600; color: #ffffff;">Channel A • 12V Primary Pack</span>
                    <span style="font-size: 0.76rem; color: #86868b; margin-left: 8px;">12,000 mAh • 44.4 Wh • 350A/1800A Peak</span>
                </div>
                <div>{get_grade_badge(grade12)}</div>
            </div>

            <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 14px;">
                <div>
                    <div class="apple-label">Terminal Voltage</div>
                    <div style="font-size: 1.5rem; font-weight: 700; color: #58a6ff;">{v12:.2f} <span style="font-size: 0.9rem; font-weight: 400;">V</span></div>
                    <div class="apple-val-sub">{f"-{sag12:.2f}V sag" if sag12 > 0 else "Resting Baseline"}</div>
                </div>
                <div>
                    <div class="apple-label">Health (SOH)</div>
                    <div style="font-size: 1.5rem; font-weight: 700; color: {'#30d158' if soh12 >= 80 else ('#ff9f0a' if soh12 >= 65 else '#86868b')};">{soh12:.1f} <span style="font-size: 0.9rem; font-weight: 400;">%</span></div>
                    <div class="apple-val-sub">{uwh12:.1f} Wh Usable</div>
                </div>
                <div>
                    <div class="apple-label">Internal Resistance</div>
                    <div style="font-size: 1.5rem; font-weight: 700; color: #f5f5f7;">{ir12:.1f} <span style="font-size: 0.9rem; font-weight: 400;">mΩ</span></div>
                    <div class="apple-val-sub">Dynamic Ohm's Law (ΔV/I)</div>
                </div>
                <div>
                    <div class="apple-label">Est. Remaining Life</div>
                    <div style="font-size: 1.5rem; font-weight: 700; color: #f5f5f7;">{rt12:.1f} <span style="font-size: 0.9rem; font-weight: 400;">hrs</span></div>
                    <div class="apple-val-sub">~{cyc12:,} stationary cycles</div>
                </div>
            </div>

            <div style="border-top: 1px solid rgba(255, 255, 255, 0.08); padding-top: 10px; display: flex; justify-content: space-between; align-items: center;">
                <div style="font-size: 0.78rem;">
                    <span style="color: #86868b;">Safety Integrity:</span> {get_safety_badge(safety12)}
                </div>
                <div style="font-size: 0.76rem; color: #86868b;">
                    Thermal Runaway Risk: <span style="color: #f5f5f7;">{safety12.get('thermal_runaway_risk', 'SAFE')}</span> • Impedance: <span style="color: #f5f5f7;">{safety12.get('impedance_state', 'NOMINAL')}</span>
                </div>
            </div>
        </div>
    """, unsafe_allow_html=True)

# CHANNEL B: 5V MOBILE POWER BANK BUFFER
with col_b:
    v5 = ch_b.get("voltage_live", 0.0)
    soh5 = ch_b.get("soh_pct", 0.0)
    ir5 = ch_b.get("internal_resistance_mohm", 0.0)
    sag5 = ch_b.get("voltage_sag", 0.0)
    grade5 = ch_b.get("grade", "Disconnected")
    safety5 = ch_b.get("safety", {})
    uwh5 = ch_b.get("usable_wh", 0.0)
    rt5 = ch_b.get("runtime_hours", 0.0)
    cyc5 = ch_b.get("cycles_remaining", 0)

    st.markdown(f"""
        <div class="apple-card">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                <div>
                    <span style="font-size: 1.05rem; font-weight: 600; color: #ffffff;">Channel B • 5V Secondary Buffer</span>
                    <span style="font-size: 0.76rem; color: #86868b; margin-left: 8px;">10,000 mAh • 37.0 Wh • 2.4A USB</span>
                </div>
                <div>{get_grade_badge(grade5)}</div>
            </div>

            <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 14px;">
                <div>
                    <div class="apple-label">Terminal Voltage</div>
                    <div style="font-size: 1.5rem; font-weight: 700; color: #00bc8c;">{v5:.2f} <span style="font-size: 0.9rem; font-weight: 400;">V</span></div>
                    <div class="apple-val-sub">{f"-{sag5:.2f}V sag" if sag5 > 0 else "Resting Baseline"}</div>
                </div>
                <div>
                    <div class="apple-label">Health (SOH)</div>
                    <div style="font-size: 1.5rem; font-weight: 700; color: {'#30d158' if soh5 >= 80 else ('#ff9f0a' if soh5 >= 65 else '#86868b')};">{soh5:.1f} <span style="font-size: 0.9rem; font-weight: 400;">%</span></div>
                    <div class="apple-val-sub">{uwh5:.1f} Wh Usable</div>
                </div>
                <div>
                    <div class="apple-label">Internal Resistance</div>
                    <div style="font-size: 1.5rem; font-weight: 700; color: #f5f5f7;">{ir5:.1f} <span style="font-size: 0.9rem; font-weight: 400;">mΩ</span></div>
                    <div class="apple-val-sub">Dynamic Ohm's Law (ΔV/I)</div>
                </div>
                <div>
                    <div class="apple-label">Est. Remaining Life</div>
                    <div style="font-size: 1.5rem; font-weight: 700; color: #f5f5f7;">{rt5:.1f} <span style="font-size: 0.9rem; font-weight: 400;">hrs</span></div>
                    <div class="apple-val-sub">~{cyc5:,} stationary cycles</div>
                </div>
            </div>

            <div style="border-top: 1px solid rgba(255, 255, 255, 0.08); padding-top: 10px; display: flex; justify-content: space-between; align-items: center;">
                <div style="font-size: 0.78rem;">
                    <span style="color: #86868b;">Safety Integrity:</span> {get_safety_badge(safety5)}
                </div>
                <div style="font-size: 0.76rem; color: #86868b;">
                    Thermal Runaway Risk: <span style="color: #f5f5f7;">{safety5.get('thermal_runaway_risk', 'SAFE')}</span> • Impedance: <span style="color: #f5f5f7;">{safety5.get('impedance_state', 'NOMINAL')}</span>
                </div>
            </div>
        </div>
    """, unsafe_allow_html=True)

# =========================================================================
# REAL-TIME VOLTAGE STRIP CHART (Apple Dark Slate Minimal Palette)
# =========================================================================
chart_col, act_col = st.columns([3.2, 1.2])

with chart_col:
    st.markdown("""
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
            <span style="font-size: 0.85rem; font-weight: 600; color: #ffffff;">Real-Time Multi-Channel Voltage Waveform</span>
            <span style="font-size: 0.75rem; color: #86868b;">5 Hz Hardware ADC Stream</span>
        </div>
    """, unsafe_allow_html=True)

    if data and "history" in data and len(data["history"]) > 0:
        df = pd.DataFrame(data["history"])
        df["Relative Time (s)"] = df["time"] - df["time"].min()

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df["Relative Time (s)"],
            y=df["v12"],
            mode="lines",
            name="Channel A (12V Pack)",
            line=dict(color="#0a84ff", width=2.4)
        ))
        fig.add_trace(go.Scatter(
            x=df["Relative Time (s)"],
            y=df["v5"],
            mode="lines",
            name="Channel B (5V Buffer)",
            line=dict(color="#30d158", width=2.4)
        ))
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="#0c0d11",
            plot_bgcolor="#0c0d11",
            xaxis=dict(
                title=dict(text="Timeline (Seconds)", font=dict(size=11, color="#86868b")),
                showgrid=True,
                gridcolor="rgba(255, 255, 255, 0.05)",
                zeroline=False,
            ),
            yaxis=dict(
                title=dict(text="Measured Voltage (V)", font=dict(size=11, color="#86868b")),
                showgrid=True,
                gridcolor="rgba(255, 255, 255, 0.05)",
                zeroline=False,
            ),
            height=240,
            margin=dict(l=35, r=20, t=10, b=25),
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1,
                font=dict(size=11, color="#f5f5f7")
            )
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Acquiring high-speed telemetry packets from FireBeetle 2 ESP32-S3 on COM18...")

with act_col:
    st.markdown("""
        <div style="font-size: 0.85rem; font-weight: 600; color: #ffffff; margin-bottom: 8px;">
            Circularity Routing Action
        </div>
    """, unsafe_allow_html=True)
    
    st.markdown(f"""
        <div class="apple-card" style="padding: 14px 16px;">
            <div class="apple-label">Autonomous State</div>
            <div style="font-size: 1rem; font-weight: 600; color: {'#30d158' if router_active else '#86868b'}; margin-bottom: 6px;">
                {'Active: Pack Routed to Solar Hub' if router_active else 'Isolated: Standby Safety Mode'}
            </div>
            <div style="font-size: 0.74rem; color: #86868b; line-height: 1.35; margin-bottom: 12px;">
                Dynamically routes healthiest Grade A packs to stationary storage and Grade B to micromobility buffers.
            </div>
        </div>
    """, unsafe_allow_html=True)
    
    if st.button("🔀 Toggle Hub Router Relay", use_container_width=True):
        new_cmd = "ROUTE_OFF" if router_active else "ROUTE_ON"
        send_hardware_command(new_cmd)

# 500 ms real-time refresh loop
time.sleep(0.5)
st.rerun()
