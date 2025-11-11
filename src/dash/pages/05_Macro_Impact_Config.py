# src/dash/pages/05_Macro_Impact_Config.py
import streamlit as st
import os
import sys
import httpx
import pandas as pd

# Add the root directory to the Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
from dash.app import check_auth

# --- API Configuration ---
API_BASE_URL = os.getenv("API_BASE_URL", "http://api:8000")
ADMIN_KEY = os.getenv("ADMIN_KEY")
HEADERS = {"X-ADMIN-KEY": ADMIN_KEY}

# --- API Functions ---
@st.cache_data(ttl=300)
def get_macro_config_from_api():
    try:
        url = f"{API_BASE_URL}/admin/macro/impact"
        response = httpx.get(url, headers=HEADERS, timeout=10.0)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        st.error(f"Failed to fetch macro config: {e}")
    return []

def save_macro_config_to_api(data):
    try:
        url = f"{API_BASE_URL}/admin/macro/impact"
        payload = {"data": data}
        response = httpx.post(url, headers=HEADERS, json=payload, timeout=10.0)
        response.raise_for_status()
        st.success("Configuration saved successfully!")
        return True
    except Exception as e:
        st.error(f"Failed to save macro config: {e}")
    return False

# --- Main Page ---
def main():
    st.set_page_config(layout="wide", page_title="Stock Hunter AI - Macro Impact Config")
    check_auth()

    st.title("Macro Sector Impact Configuration")

    config_data = get_macro_config_from_api()

    if not config_data:
        st.warning("Could not load macro impact configuration.")
        st.stop()

    df = pd.DataFrame(config_data)

    edited_df = st.data_editor(df, num_rows_to_add=1, use_container_width=True)

    if st.button("Save Changes", type="primary"):
        updated_data = edited_df.to_dict('records')
        if save_macro_config_to_api(updated_data):
            st.cache_data.clear()
            st.rerun()

if __name__ == "__main__":
    main()
