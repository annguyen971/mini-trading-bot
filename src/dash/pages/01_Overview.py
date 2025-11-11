# src/dash/pages/01_Overview.py
import streamlit as st
import os
import sys
import httpx
import pandas as pd
import plotly.graph_objects as go

# Add the root directory to the Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app import check_auth

# --- API Configuration ---
API_BASE_URL = os.getenv("API_BASE_URL", "http://api:8000")
ADMIN_KEY = os.getenv("ADMIN_KEY")
HEADERS = {"X-ADMIN-KEY": ADMIN_KEY}

# --- API Functions ---
@st.cache_data(ttl=60)
def get_predict_data(symbol="VN30"):
    try:
        url = f"{API_BASE_URL}/predict?symbol={symbol}"
        response = httpx.get(url, headers=HEADERS, timeout=10.0)
        if response.status_code == 200:
            return response.json(), None
        error_message = response.json().get("detail", "Unknown API error")
        return None, (response.status_code, error_message)
    except httpx.RequestError as e:
        return None, (503, f"Service Unavailable: {e}")

# --- UI Components ---
def create_regime_chart(chart_data):
    if not chart_data:
        st.info("No chart data available from the API.")
        return go.Figure()

    df = pd.DataFrame(chart_data)
    df['date'] = pd.to_datetime(df['date'])
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df['date'], y=df['price'], mode='lines', name='Price'))

    colors = {"Accumulation": "green", "Burst": "yellow", "Distribution": "red", "Neutral": "grey"}
    df['regime_shifted'] = df['regime'].shift(1, fill_value=df['regime'].iloc[0])
    df['regime_changed'] = df['regime'] != df['regime_shifted']

    start_date = df['date'].iloc[0]
    for i, row in df[df['regime_changed']].iterrows():
        end_date = row['date']
        regime = df['regime_shifted'].loc[i]
        fig.add_vrect(x0=start_date, x1=end_date, fillcolor=colors.get(regime, "grey"), opacity=0.2, line_width=0, annotation_text=regime, annotation_position="top left")
        start_date = end_date

    last_regime = df['regime'].iloc[-1]
    fig.add_vrect(x0=start_date, x1=df['date'].iloc[-1], fillcolor=colors.get(last_regime, "grey"), opacity=0.2, line_width=0, annotation_text=last_regime, annotation_position="top left")

    fig.update_layout(title_text="Market Regime Overview")
    return fig

# --- Main Page ---
def main():
    st.set_page_config(layout="wide", page_title="Stock Hunter AI - Overview")
    check_auth()
    st.title("Signal Overview")

    data, error = get_predict_data()

    if error:
        status_code, message = error
        st.error(f"**API Error ({status_code}):** {message}", icon="🔥")
        if status_code == 503:
            st.error(f"**SYSTEM DEGRADED OR OFFLINE:** {message}", icon="🚨")
        st.stop()

    if data:
        if data.get("safety_banner"):
             st.warning(data["safety_banner"], icon="⚠️")

        kill_switch_on = st.toggle("Activate Kill-Switch")
        if kill_switch_on:
            st.error("Kill-Switch is active.")

        st.divider()

        col1, col2, col3 = st.columns(3)
        col1.metric("Regime", data.get("regime", "N/A").upper())
        col2.metric("HunterScore", f"{data.get('HunterScore', 0):.1f}")
        col3.metric("FrothScore", f"{data.get('FrothScore', 0):.1f}")

        st.divider()

        chart_data = data.get('chart_data')
        st.plotly_chart(create_regime_chart(chart_data), use_container_width=True)
        st.info(f"**Plain Explainer:** {data.get('plain_explainer', 'N/A')}")

    with st.sidebar:
        st.header("🔬 AI Hand-off")
        @st.cache_data(ttl=120)
        def get_export_context(format="md"):
            try:
                url = f"{API_BASE_URL}/export/context?format={format}"
                response = httpx.get(url, headers=HEADERS, timeout=15.0)
                response.raise_for_status()
                return response.text
            except Exception as e:
                st.error(f"Error fetching context: {e}")
            return None

        @st.cache_data(ttl=120)
        def get_export_pack():
            try:
                url = f"{API_BASE_URL}/export/pack"
                response = httpx.get(url, headers=HEADERS, timeout=30.0)
                response.raise_for_status()
                return response.content
            except Exception as e:
                st.error(f"Error fetching pack: {e}")
            return None

        md_context = get_export_context("md")
        if md_context:
            st.download_button("Copy Prompt for Gemini (MD)", md_context, f"prompt_{data.get('symbol', 'data')}.md", "text/markdown")

        zip_pack = get_export_pack()
        if zip_pack:
            st.download_button("Download Full Data Pack (.zip)", zip_pack, f"ai_pack_{data.get('symbol', 'data')}.zip", "application/zip")

        with st.expander("Model & Data Info"):
            st.caption(f"Model: `{data.get('model_version', 'N/A')}`")
            st.caption(f"Features: `{data.get('feature_set_version', 'N/A')}`")

if __name__ == "__main__":
    main()
