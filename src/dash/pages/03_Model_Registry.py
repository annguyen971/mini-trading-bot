import streamlit as st
import httpx
import os
import pandas as pd

# --- Configuration ---
API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
ADMIN_KEY = os.getenv("ADMIN_KEY", "supersecretkey")
HEADERS = {"X-ADMIN-KEY": ADMIN_KEY}

# --- API Functions ---
@st.cache_data(ttl=30)
def get_models():
    """Fetches the list of models from the registry."""
    try:
        url = f"{API_BASE_URL}/admin/models"
        response = httpx.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        st.error(f"HTTP error fetching models: {e.response.status_code} - {e.response.text}")
    except httpx.RequestError as e:
        st.error(f"Connection error fetching models: {e}")
    return []

def activate_model(model_version: str, mode: str):
    """Sends a request to activate a model in canary or production mode."""
    try:
        url = f"{API_BASE_URL}/admin/model/activate"
        payload = {"model_version": model_version, "mode": mode}
        response = httpx.post(url, headers=HEADERS, json=payload, timeout=15)
        response.raise_for_status()
        st.success(f"Successfully sent activation request for {model_version} in '{mode}' mode.")
        # Invalidate cache to show updated status
        st.cache_data.clear()
        return True
    except httpx.HTTPStatusError as e:
        st.error(f"HTTP error activating model: {e.response.status_code} - {e.response.text}")
    except httpx.RequestError as e:
        st.error(f"Connection error activating model: {e}")
    return False

# --- UI Layout ---
st.set_page_config(layout="wide")
st.title("📦 Model Registry Management")

st.info("Here you can manage the lifecycle of trained models, from canary testing to full promotion.")

models_data = get_models()

if models_data:
    df = pd.DataFrame(models_data)
    # Improve display
    st.dataframe(df.style.highlight_max(axis=0, subset=['metrics'], props='color:lightgreen;'))

    st.header("Actions")
    selected_model = st.selectbox(
        "Select a model version to act on:",
        options=df['model_version'].tolist()
    )

    if selected_model:
        col1, col2, col3 = st.columns([1, 1, 5])
        with col1:
            if st.button("Approve Canary"):
                activate_model(selected_model, "canary")
                st.rerun()

        with col2:
            if st.button("Promote to Production"):
                # Add a confirmation step for a critical action
                if st.checkbox(f"I confirm promoting {selected_model} to production."):
                    activate_model(selected_model, "production")
                    st.rerun()
else:
    st.warning("No models found in the registry or could not connect to the API.")
