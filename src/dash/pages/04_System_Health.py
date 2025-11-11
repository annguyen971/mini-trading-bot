# src/dash/pages/04_System_Health.py
import streamlit as st
import os
import sys
import httpx
import pandas as pd
import plotly.graph_objects as go

# Add the root directory to the Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
from dash.app import check_auth

# --- API Configuration ---
API_BASE_URL = os.getenv("API_BASE_URL", "http://api:8000")
ADMIN_KEY = os.getenv("ADMIN_KEY")
HEADERS = {"X-ADMIN-KEY": ADMIN_KEY}

# --- API Functions ---
@st.cache_data(ttl=300)
def get_health_stats_from_api():
    try:
        url = f"{API_BASE_URL}/admin/health/macro_stats"
        response = httpx.get(url, headers=HEADERS, timeout=10.0)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        st.error(f"Failed to fetch health stats: {e}")
    return {}

# --- UI Components ---
def create_sector_heatmap(heatmap_data):
    if not heatmap_data:
        return go.Figure()

    df = pd.DataFrame(heatmap_data)
    z = df[['momentum', 'breadth']].values.T
    x = df['sector'].tolist()
    y = ['Momentum', 'Breadth']

    fig = go.Figure(data=go.Heatmap(z=z, x=x, y=y, colorscale='Viridis'))
    fig.update_layout(title='Sector Momentum and Breadth Heatmap')
    return fig

# --- Main Page ---
def main():
    st.set_page_config(layout="wide", page_title="Stock Hunter AI - System Health")
    check_auth()

    st.title("System Health & Macro Overview")

    stats = get_health_stats_from_api()

    if not stats:
        st.warning("Could not retrieve system health data.")
        st.stop()

    st.header("Macro Mini-Hub")
    macro_data = stats.get("macro_mini_hub", [])

    cols = st.columns(len(macro_data) if macro_data else 1)
    for i, item in enumerate(macro_data):
        cols[i].metric(label=item['metric_name'], value=item['value'], delta=item.get('delta'))

    st.divider()

    st.header("Sector Heatmap")
    heatmap_data = stats.get("sector_heatmap", [])
    if heatmap_data:
        fig = create_sector_heatmap(heatmap_data)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No sector heatmap data available.")

if __name__ == "__main__":
    main()
