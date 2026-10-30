# frontend/app.py
# Streamlit dashboard. Calls the FastAPI backend over HTTP and falls back to the
# in process backend functions only when the API cannot be reached.
import os
import sys

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000").rstrip("/")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8100").rstrip("/")
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

st.set_page_config(page_title="SecondPulse | Climathon 2026 Track 3", page_icon="🔋", layout="wide")


@st.cache_resource
def local_backend():
    """Import the backend only when it is needed, because importing it trains the SOH model."""
    if ROOT not in sys.path:
        sys.path.append(ROOT)
    from backend import main
    return main


modes = set()


def fetch(method, path, payload=None):
    """Call the API. If it is unreachable, run the same function in process."""
    try:
        r = requests.request(method, BACKEND_URL + path, json=payload, timeout=(1.0, 15.0))
    except (requests.ConnectionError, requests.Timeout):
        modes.add("local")
        b = local_backend()
        if path == "/api/oem-models":
            return b.OEM_BATTERY_DB
        if path == "/api/assumptions":
            return b.ASSUMPTIONS
        if path == "/api/triage":
            return b.evaluate_battery_pack(b.TriageRequest(**payload)).model_dump()
        if path == "/api/can-gateway-simulate":
            return b.simulate_can_gateway(b.GatewaySimRequest(**payload)).model_dump()
        raise
    r.raise_for_status()
    modes.add("api")
    return r.json()


def fetch_gateway_state():
    """Read the state page of the Pi gateway. Returns None when it cannot be reached."""
    try:
        r = requests.get(GATEWAY_URL + "/state", timeout=(0.5, 1.5))
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError):
        return None


def tab2_source(gw_state, force_sim):
    """Which source Tab 2 shows: live Pi, demo bus, or pure simulation."""
    if gw_state is None or force_sim:
        return "sim", "Pure simulation"
    if gw_state.get("source") == "live":
        return "live", "Live Pi"
    return "demo", f"Demo bus ({gw_state.get('bus', 'unknown')})"


badge = st.sidebar.empty()
badge_tab2 = st.sidebar.empty()
force_sim = st.sidebar.checkbox("Use the software simulation in Tab 2 even if the gateway answers", value=False)
gateway_state = fetch_gateway_state()
tab2_kind, tab2_label = tab2_source(gateway_state, force_sim)

oem_db = fetch("GET", "/api/oem-models")
A = fetch("GET", "/api/assumptions")
capex = A["capex_eur_per_kwh"]
hub = A["hub"]

st.title("SecondPulse: retired EV battery triage and CAN gateway demo")
st.caption(
    "Climathon 2026 Track 3. Euro, carbon and lifetime figures are project assumptions, not measurements."
)

tab1, tab2, tab3, tab4 = st.tabs([
    "1. AI/ML SOH Triage & Safety Gate",
    "2. Live CAN Bus Gateway",
    "3. Riga Micromobility Hub & ROI Calculator",
    "4. Hardware Architecture & Protection Specs",
])

# ===========================================================================
# TAB 1
# ===========================================================================
with tab1:
    st.subheader("Step 1: Fleet diagnostic and 3 tier repurposing triage")
    st.info(
        "The SOH model is trained on synthetic data generated from a formula, not on measured battery data. "
        "It demonstrates the pipeline. Replace the training data (for example with NASA or CALCE datasets) "
        "before treating any SOH value as a prediction for a real pack."
    )
    col_in, col_out = st.columns([1, 1.4])

    with col_in:
        oem_choice = st.selectbox("Select retired EV battery pack model", list(oem_db.keys()))
        cycle_count = st.slider("Historical vehicle charge cycles", 100, 3000, 1100, step=50)
        fast_charge_ratio = st.slider("DC fast charging share (0.0 = AC only, 1.0 = DC only)", 0.0, 1.0, 0.22, step=0.02)
        avg_temp_c = st.slider("Historical average operating temperature (°C)", 5.0, 45.0, 21.0, step=1.0)
        internal_res_mohm = st.slider("Measured pack internal resistance (mΩ)", 20.0, 120.0, 46.0, step=1.0)
        cell_delta_mv = st.slider("Max cell voltage spread at rest (mV)", 5.0, 180.0, 22.0, step=1.0)

    res = fetch("POST", "/api/triage", {
        "oem_model": oem_choice,
        "cycle_count": cycle_count,
        "fast_charge_ratio": fast_charge_ratio,
        "avg_temp_c": avg_temp_c,
        "internal_res_mohm": internal_res_mohm,
        "cell_delta_mv": cell_delta_mv,
    })

    with col_out:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric(
            "Predicted SOH", f"{res['predicted_soh_pct']:.1f}%",
            f"Tree spread {res['soh_ci_90_lower']:.1f} to {res['soh_ci_90_upper']:.1f}%", delta_color="off",
        )
        m2.metric("Usable energy", f"{res['usable_kwh']:.1f} kWh")
        m3.metric(
            "Remaining second life (assumed fade)", f"{res['estimated_second_life_rul_cycles']:,} cycles",
            f"about {res['estimated_second_life_years']} years at 0.2C or less", delta_color="off",
        )
        if res["capex_eur_per_kwh"] is None:
            m4.metric("System cost (assumption)", "n/a", "pack is blocked", delta_color="off")
        else:
            m4.metric(
                "System cost (assumption)", f"€{res['capex_eur_per_kwh']:.0f}/kWh",
                f"{res['cost_savings_pct']:.1f}% below new LFP at €{res['new_lfp_benchmark_eur_per_kwh']:.0f}/kWh",
                delta_color="off",
            )
        st.caption("Tree spread is the 5th to 95th percentile of the individual tree outputs, not a statistical confidence interval.")

        body = f"**Safety status:** {res['safety_status']}\n\n**Assigned pathway:** {res['assigned_tier']}\n\n{res['recommended_application']}"
        if res["tier_number"] == 3:
            st.error(body)
        elif res["tier_number"] == 1:
            st.success(body)
        else:
            st.warning(body)

        labor = A["labor_hours_per_kwh"]
        df_econ = pd.DataFrame([
            {"Pathway": "Tier 1: whole pack + CAN gateway", "CapEx (€/kWh)": capex["tier1"], "Labor (hrs/kWh)": labor["tier1"]},
            {"Pathway": "Tier 2: intact 48V module swap", "CapEx (€/kWh)": capex["tier2"], "Labor (hrs/kWh)": labor["tier2"]},
            {"Pathway": "Cell level teardown (hazardous)", "CapEx (€/kWh)": capex["teardown"], "Labor (hrs/kWh)": labor["teardown"]},
            {"Pathway": "New commercial LFP storage", "CapEx (€/kWh)": capex["new_lfp"], "Labor (hrs/kWh)": labor["new_lfp"]},
        ])
        fig_econ = px.bar(
            df_econ, x="CapEx (€/kWh)", y="Pathway", orientation="h", text="CapEx (€/kWh)",
            color="Pathway", hover_data=["Labor (hrs/kWh)"],
            title="Installed cost per kWh by pathway (project assumptions, not measurements)",
        )
        fig_econ.update_layout(showlegend=False, height=300)
        st.plotly_chart(fig_econ, use_container_width=True)

# ===========================================================================
# TAB 2
# ===========================================================================
def render_gateway(gw):
    """Show one gateway state. The same layout is used for the Pi gateway and for the simulation."""
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("HV contactor", gw["contactor_state"], gw["contactor_detail"], delta_color="off")
    k2.metric("DC bus voltage", f"{gw['pack_voltage_v']} V", f"Cell spread {gw['cell_delta_mv']} mV", delta_color="off")
    k3.metric("Charge current limit", f"{gw['max_charge_a']} A", "0.2C ceiling with derating", delta_color="off")
    k4.metric("Discharge current limit", f"{gw['max_discharge_a']} A", gw["protection_alarm"].split("(")[0].strip(), delta_color="off")

    if "precharge_relay" in gw:
        st.caption(
            f"Precharge relay {'ON' if gw['precharge_relay'] else 'off'}, main relay {'ON' if gw['main_relay'] else 'off'}, "
            f"enable input {'high' if gw['enable_high'] else 'low'}, inverter frames sent {gw['tx_count']}, "
            f"send errors {gw['tx_errors']}, bus {gw['bus']}, GPIO {gw['gpio']}."
        )
        if gw["gpio"] == "mock":
            st.warning("GPIO is mocked on this gateway, so the relay states above do not switch anything real.")
    st.info(f"**Isolation status:** {gw['isolation_status']}. iso_610h_masked = {str(gw['iso_610h_masked']).lower()}")
    if gw["protection_alarm"] != "NONE":
        st.error(f"Protection: {gw['protection_alarm']}")

    col_rx, col_tx = st.columns(2)
    with col_rx:
        st.markdown("#### Pack side frames")
        st.caption("Frames read from the BMS. In the software simulation only the CAN IDs are shown, as reference labels.")
        st.dataframe(pd.DataFrame(gw["oem_rx_frames"]), use_container_width=True)
    with col_tx:
        st.markdown("#### Inverter side frames (8 byte payloads, little endian)")
        st.dataframe(
            pd.DataFrame(gw["inverter_tx_frames"])[["can_id", "tx_status", "payload_hex", "decoded"]],
            use_container_width=True,
        )

    if gw["cell_voltages"]:
        cell_df = pd.DataFrame({"Cell #": range(1, len(gw["cell_voltages"]) + 1), "Voltage (V)": gw["cell_voltages"]})
        fig_cells = px.bar(
            cell_df, x="Cell #", y="Voltage (V)",
            title=f"Simulated cell voltages ({len(gw['cell_voltages'])}S pack, min {gw['min_cell_v']} V, max {gw['max_cell_v']} V)",
        )
        fig_cells.update_yaxes(range=[3.0, 4.3])
        fig_cells.update_layout(height=280)
        st.plotly_chart(fig_cells, use_container_width=True)


@st.fragment(run_every="1s")
def live_gateway_panel():
    state = fetch_gateway_state()
    if state is None:
        st.warning(f"The gateway at {GATEWAY_URL} stopped answering. Reload the page to switch to the simulation.")
        return
    render_gateway(state)


with tab2:
    st.subheader("Step 2: OEM BMS to solar inverter protocol translation")
    if tab2_kind == "sim":
        st.markdown(
            "Source: **pure software simulation**. No gateway is running and no CAN hardware is involved. "
            "It shows the frames a gateway would send to the inverter, using a Pylontech style layout. "
            "Cell spread and SOH come from Tab 1."
        )
        c1, c2, c3 = st.columns(3)
        with c1:
            sim_soc = st.slider("Pack state of charge (%)", 5.0, 98.0, 78.0, step=1.0)
            sim_temp = st.slider("Pack temperature (°C)", -5.0, 50.0, 24.5, step=0.5)
        with c2:
            sim_ms = st.slider(
                "Time since inverter enable line went high (ms)", 0, 4000, 2000, step=100,
                help="Below 1,500 ms the gateway holds CAN TX. 1,500 ms is a design value of this project.",
            )
            iso_pulse = st.checkbox("Inverter isolation test pulse active", value=False)
        with c3:
            imbalance_fault = st.checkbox("Inject cell imbalance fault (140 mV or more)", value=False)

        gw = fetch("POST", "/api/can-gateway-simulate", {
            "oem_model": oem_choice,
            "pack_soc_pct": sim_soc,
            "pack_soh_pct": res["predicted_soh_pct"],
            "pack_temp_c": sim_temp,
            "time_since_enable_ms": sim_ms,
            "inverter_isolation_pulse_active": iso_pulse,
            "cell_imbalance_fault": imbalance_fault,
            "cell_delta_mv": cell_delta_mv,
        })
        render_gateway(gw)
    else:
        what = (
            "the **Raspberry Pi gateway** reading a real BMS and driving real relays"
            if tab2_kind == "live"
            else "the **gateway in demo mode**. A fake BMS feeds it and no real hardware is involved"
        )
        st.markdown(f"Source: {what}, at `{GATEWAY_URL}`. The view refreshes every second. The sliders belong to the software simulation and are hidden here.")
        live_gateway_panel()

# ===========================================================================
# TAB 3
# ===========================================================================
with tab3:
    st.subheader("Step 3: Riga deployment sizing")
    col_l, col_r = st.columns(2)

    with col_l:
        hub_site = st.selectbox("Select Riga pilot site", [
            "RTU Ķīpsala Campus Micromobility Hub (Āzenes iela)",
            "Riga Airport (RIX) Employee E Bike and Shuttle Buffer",
            "Bolderāja / Imanta Transit Mobility Point",
            "AS 'Sadales tīkls' Secondary Substation Peak Shaving Buffer",
            "SIA 'Rīgas Meži' Off Grid Forestry Drone and Tool Charging Trailer",
        ])
        num_packs = st.slider("Number of identical packs assumed in parallel", 1, 3, 1)
        solar_kwp = st.slider("Attached solar PV array size (kWp)", 2.0, 30.0, 8.0, step=1.0)

    with col_r:
        if res["tier_number"] == 3:
            st.warning("The pack selected in Tab 1 is blocked, so no hub is sized from it. Choose another pack or inputs in Tab 1.")
        else:
            tier_key = f"tier{res['tier_number']}"
            total_kwh = round(res["usable_kwh"] * num_packs, 1)
            daily_kwh = round(min(total_kwh * hub["usable_depth_of_discharge"], solar_kwp * hub["solar_kwh_per_kwp_per_day"]), 1)
            scooter_charges = int(daily_kwh / hub["kwh_per_escooter_charge"])
            ebike_charges = int(daily_kwh / hub["kwh_per_ebike_charge"])
            cost_here = round(total_kwh * capex[tier_key])
            cost_new = round(total_kwh * capex["new_lfp"])

            r1, r2, r3 = st.columns(3)
            r1.metric("Hub storage capacity", f"{total_kwh} kWh", f"{num_packs} x {oem_choice.split('(')[0].strip()}", delta_color="off")
            r2.metric("Daily charges (assumed)", f"{scooter_charges} scooters", f"or {ebike_charges} cargo e bikes", delta_color="off")
            r3.metric(
                "Hardware cost difference (assumed)", f"€{cost_new - cost_here:,}",
                f"€{cost_here:,} vs €{cost_new:,} for new LFP", delta_color="off",
            )
            st.success(
                f"**Pilot sketch for {hub_site}:** {num_packs} x {oem_choice} gives {total_kwh} kWh of buffer storage "
                f"and avoids about {res['avoided_co2e_kg'] * num_packs:,.0f} kg CO₂e of battery manufacturing, "
                f"with hardware cost {res['cost_savings_pct']:.1f}% below new LFP ({res['assigned_tier'].split(':')[0]})."
            )
            st.caption(
                "Project assumptions, not measurements: cost per kWh, carbon factor, "
                f"{hub['usable_depth_of_discharge']:.0%} usable depth, {hub['solar_kwh_per_kwp_per_day']} kWh per kWp per day, "
                f"{hub['kwh_per_escooter_charge']} kWh per scooter charge, {hub['kwh_per_ebike_charge']} kWh per cargo e bike charge."
            )

# ===========================================================================
# TAB 4
# ===========================================================================
with tab4:
    st.subheader("Step 4: Bench and field wiring specification")
    st.markdown("### Bill of materials for one gateway node")
    bom = A["gateway_bom_eur"]
    bom_df = pd.DataFrame([
        {"Component": b["item"], "Unit cost": f"€{b['eur']:.2f}" if b["eur"] is not None else "not priced", "Role": b["role"]}
        for b in bom
    ])
    st.table(bom_df)
    priced = [b["eur"] for b in bom if b["eur"] is not None]
    st.metric("Total of priced items (assumed prices)", f"€{sum(priced):.2f}")
    st.caption(
        "Prices are project assumptions, not quotes. Items marked not priced are left out of the total. "
        "A Raspberry Pi running Linux is not a safety rated controller. The hardware fuse, the battery's own BMS and the "
        "contactors must protect the system, and the Pi must never be the only safeguard."
    )

# ===========================================================================
# Sidebar mode badge, written last so it reflects every call made in this run
# ===========================================================================
if modes == {"api"}:
    badge.success(f"Mode: API\n\n{BACKEND_URL}")
elif modes == {"local"}:
    badge.warning(f"Mode: in process fallback\n\nAPI at {BACKEND_URL} is unreachable.")
else:
    badge.warning(f"Mode: mixed\n\nSome calls reached {BACKEND_URL}, some fell back to in process.")

badge_tab2.info(f"Tab 2 source: {tab2_label}" + (f"\n\n{GATEWAY_URL}" if tab2_kind != "sim" else ""))
