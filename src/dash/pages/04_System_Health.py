import streamlit as st
import httpx
import os
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt

# --- Configuration ---
API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
ADMIN_KEY = os.getenv("ADMIN_KEY", "supersecretkey")
HEADERS = {"X-ADMIN-KEY": ADMIN_KEY}

# --- API Function ---
@st.cache_data(ttl=300)  # Cache for 5 minutes
def get_macro_stats():
    """Fetches macro and sector health statistics."""
    try:
        url = f"{API_BASE_URL}/admin/health/macro_stats"
        response = httpx.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        st.error(f"HTTP error fetching health stats: {e.response.status_code} - {e.response.text}")
    except httpx.RequestError as e:
        st.error(f"Connection error fetching health stats: {e}")
    return None

# --- UI Layout ---
st.set_page_config(layout="wide")
st.title("🌐 System Health & Macro Dashboard")

health_data = get_macro_stats()

if health_data:
    # --- Macro Mini-Hub ---
    st.header("Vĩ mô Mini-Hub")
    macro_hub = health_data.get("macro_mini_hub", {})
    if macro_hub:
        cols = st.columns(len(macro_hub))
        for i, (metric, value) in enumerate(macro_hub.items()):
            cols[i].metric(label=metric.replace('_', ' ').title(), value=f"{value:.2f}")
    else:
        st.info("No Macro Mini-Hub data available.")

    # --- Sector Heatmap ---
    st.header("Sector Performance Heatmap")
    sector_heatmap = health_data.get("sector_heatmap", [])
    if sector_heatmap:
        df = pd.DataFrame(sector_heatmap)

        # Check if the dataframe is empty or doesn't have the expected columns
        if not df.empty and all(col in df.columns for col in ['sector', 'metric', 'value']):
            try:
                # Pivot the table to create a matrix suitable for a heatmap
                heatmap_df = df.pivot(index="sector", columns="metric", values="value")

                # Generate the heatmap using seaborn
                fig, ax = plt.subplots(figsize=(10, 8))
                sns.heatmap(heatmap_df, annot=True, fmt=".2f", cmap="viridis", ax=ax, linewidths=.5)
                ax.set_title("Sector Momentum & Breadth")
                ax.set_xlabel("Metrics")
                ax.set_ylabel("Sectors")

                # Display the plot in Streamlit
                st.pyplot(fig)
            except Exception as e:
                st.error(f"Could not generate heatmap: {e}")
                st.dataframe(df) # Display raw data as a fallback
        else:
            st.warning("Heatmap data is not in the expected format.")
            st.dataframe(df) # Display raw data if format is incorrect

    else:
        st.info("No Sector Heatmap data available.")
else:
    st.warning("Could not retrieve system health data from the API.")
