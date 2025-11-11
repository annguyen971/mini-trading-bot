# src/dash/pages/01_Overview.py
import streamlit as st
import os
import sys
import httpx
import pandas as pd
import plotly.graph_objects as go

# Add the root directory to the Python path to allow imports from `dash`
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app import check_auth

# --- API Configuration ---
API_BASE_URL = os.getenv("API_BASE_URL", "http://api:8000")
ADMIN_KEY = os.getenv("ADMIN_KEY")
HEADERS = {"X-ADMIN-KEY": ADMIN_KEY}

@st.cache_data(ttl=60) # Cache for 60 seconds
def get_predict_data(symbol="VN30"):
    """Fetches prediction data from the AppAPI."""
    try:
        url = f"{API_BASE_URL}/predict?symbol={symbol}"
        response = httpx.get(url, headers=HEADERS, timeout=10.0)

        if response.status_code == 200:
            return response.json(), None
        else:
            error_message = response.json().get("detail", "Unknown error")
            return None, (response.status_code, error_message)

    except httpx.RequestError as e:
        return None, (503, f"Service Unavailable: {e}")

def create_regime_chart():
    """Creates a placeholder plotly chart for the market regime."""
    # This would normally be populated with actual data from the API
    df = pd.DataFrame({
        "date": pd.to_datetime(['2025-10-01', '2025-10-02', '2025-10-03', '2025-10-04', '2025-10-05']),
        "price": [100, 102, 101, 103, 105],
        "regime": ["Accumulation", "Accumulation", "Burst", "Burst", "Burst"]
    })

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df['date'], y=df['price'], mode='lines', name='Price'))

    # Add shaded regions for regimes
    # This is a simplified example. A real implementation would loop through regime changes.
    fig.add_vrect(x0="2025-10-01", x1="2025-10-03",
                  annotation_text="Accumulation", annotation_position="top left",
                  fillcolor="green", opacity=0.25, line_width=0)
    fig.add_vrect(x0="2025-10-03", x1="2025-10-05",
                  annotation_text="Burst", annotation_position="top left",
                  fillcolor="yellow", opacity=0.25, line_width=0)

    fig.update_layout(title_text="Market Regime Overview")
    return fig

def main():
    st.set_page_config(layout="wide", page_title="Stock Hunter AI - Overview")
    check_auth()

    st.title("Signal Overview")

    # --- Data Fetching ---
    data, error = get_predict_data()

    # --- Safety Banner & Error Handling (AC1) ---
    if error:
        status_code, message = error
        if status_code == 503: # Service Unavailable (e.g., Kill-switch ON, stale data)
            st.error(f"**SYSTEM DEGRADED OR OFFLINE:** {message}", icon="🚨")
        else:
            st.error(f"**API Error ({status_code}):** {message}", icon="🔥")
        st.stop() # Halt execution if we can't get data

    # --- Main Dashboard ---
    if data:
        # Display safety banner if present in the data payload
        if data.get("safety_banner"):
             st.warning(data["safety_banner"], icon="⚠️")

        # --- Kill-Switch (AC1) ---
        # This is a simplified UI control. The actual state is managed by the API.
        kill_switch_on = st.toggle("Activate Kill-Switch", help="Immediately halt all predictions and signal exports.")
        if kill_switch_on:
            st.error("Kill-Switch is active. To resume, please toggle off and contact support if needed.")
            # In a real app, this would trigger a POST to /admin/kill-switch

        st.divider()

        # --- HunterScore & FrothScore Badges (AC1) ---
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric(label="Regime", value=data.get("regime", "N/A").upper())
        with col2:
            st.metric(label="HunterScore", value=f"{data.get('HunterScore', 0):.1f}",
                      help="Measures the quality of the 'smart money' signal before a breakout.")
        with col3:
            st.metric(label="FrothScore", value=f"{data.get('FrothScore', 0):.1f}",
                      help="Measures the level of 'noise' and baseless hype.", delta_color="inverse")

        st.divider()

        # --- Regime Chart & Explainer (AC1) ---
        st.plotly_chart(create_regime_chart(), use_container_width=True)

        st.info(f"**Plain Explainer:** {data.get('plain_explainer', 'No explanation available.')}")

    # --- AI Hand-off Panel (Sidebar) ---
    with st.sidebar:
        st.header("🔬 AI Hand-off")
        st.write("Export the current signal context for analysis with external AI tools like Gemini or ChatGPT.")

        @st.cache_data(ttl=120)
        def get_export_context(format="md"):
            try:
                url = f"{API_BASE_URL}/export/context?format={format}"
                response = httpx.get(url, headers=HEADERS, timeout=15.0)
                response.raise_for_status()
                return response.text
            except httpx.HTTPStatusError as e:
                st.error(f"Error fetching context: {e.response.json().get('detail', 'Unknown')}")
            except httpx.RequestError as e:
                st.error(f"Network error: {e}")
            return None

        @st.cache_data(ttl=120)
        def get_export_pack():
            try:
                url = f"{API_BASE_URL}/export/pack"
                response = httpx.get(url, headers=HEADERS, timeout=30.0)
                response.raise_for_status()
                return response.content
            except httpx.HTTPStatusError as e:
                st.error(f"Error fetching pack: {e.response.json().get('detail', 'Unknown')}")
            except httpx.RequestError as e:
                st.error(f"Network error: {e}")
            return None

        md_context = get_export_context("md")
        if md_context:
            st.download_button(
                label="Copy Prompt for Gemini (MD)",
                data=md_context,
                file_name=f"stock_hunter_prompt_{data.get('symbol', 'VN30')}.md",
                mime="text/markdown",
            )

        zip_pack = get_export_pack()
        if zip_pack:
            st.download_button(
                label="Tải Gói Dữ liệu (Full Data Pack) (.zip)",
                data=zip_pack,
                file_name=f"ai_pack_{data.get('symbol', 'VN30')}.zip",
                mime="application/zip",
            )

        with st.expander("Model & Data Info"):
            st.caption(f"Model Version: `{data.get('model_version', 'N/A')}`")
            st.caption(f"Feature Set: `{data.get('feature_set_version', 'N/A')}`")

if __name__ == "__main__":
    main()
