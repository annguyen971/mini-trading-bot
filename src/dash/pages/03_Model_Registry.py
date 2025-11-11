# src/dash/pages/03_Model_Registry.py
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
@st.cache_data(ttl=60)
def get_models_from_api():
    try:
        url = f"{API_BASE_URL}/admin/models"
        response = httpx.get(url, headers=HEADERS, timeout=10.0)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        st.error(f"Failed to fetch models: {e}")
    return []

def activate_model_api(model_version, mode):
    try:
        url = f"{API_BASE_URL}/admin/model/activate"
        payload = {"model_version": model_version, "mode": mode}
        response = httpx.post(url, headers=HEADERS, json=payload, timeout=10.0)
        response.raise_for_status()
        st.success(f"Successfully executed '{mode}' for model `{model_version}`.")
        return True
    except Exception as e:
        st.error(f"Failed to activate model: {e}")
    return False

# --- Main Page ---
def main():
    st.set_page_config(layout="wide", page_title="Stock Hunter AI - Model Registry")
    check_auth()

    st.title("Model Registry")

    if st.button("Refresh"):
        st.cache_data.clear()
        st.rerun()

    models = get_models_from_api()

    if not models:
        st.warning("No models found in the registry.")
        st.stop()

    df = pd.DataFrame(models)
    display_cols = ['model_version', 'is_active', 'promotion_suggestion', 'model_type', 'created_at', 'metrics']
    df_display = df[[col for col in display_cols if col in df.columns]]

    st.dataframe(df_display, use_container_width=True)

    st.divider()
    st.header("Model Actions")

    selected_version = st.selectbox("Select Model Version", options=df['model_version'].tolist())

    if selected_version:
        col1, col2, col3 = st.columns(3)
        if col1.button("Approve Canary", use_container_width=True):
            if activate_model_api(selected_version, "canary"):
                st.cache_data.clear()
                st.rerun()
        if col2.button("Promote to 100%", type="primary", use_container_width=True):
             if activate_model_api(selected_version, "promote"):
                st.cache_data.clear()
                st.rerun()
        if col3.button("Rollback (Deactivate)", use_container_width=True):
            if activate_model_api(selected_version, "rollback"):
                st.cache_data.clear()
                st.rerun()

if __name__ == "__main__":
    main()
