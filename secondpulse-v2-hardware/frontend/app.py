# frontend/app.py
# BLINC: Battery Life & Intelligence Node Controller
# Authentic Apple Engineering Interface (Light & Dark Mode)
# Zero-Slop Factual Telemetry • Hardware Canvas Waveform • Pure Real-Time Sync

import os
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(
    page_title="BLINC Battery Intelligence",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Strip default Streamlit margins for edge-to-edge layout
st.markdown("""
<style>
    header[data-testid="stHeader"] { display: none !important; }
    footer { display: none !important; }
    .block-container {
        padding: 0 !important;
        margin: 0 !important;
        max-width: 100% !important;
    }
    iframe {
        border: none !important;
        width: 100vw !important;
        height: 100vh !important;
        display: block !important;
    }
</style>
""", unsafe_allow_html=True)

html_file_path = os.path.join(os.path.dirname(__file__), "index.html")
with open(html_file_path, "r", encoding="utf-8") as f:
    html_content = f.read()

components.html(html_content, height=880, scrolling=False)
