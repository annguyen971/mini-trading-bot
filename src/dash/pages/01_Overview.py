import streamlit as st
import httpx
import os
import json

# --- Configuration ---
# Use localhost for local development if API_BASE_URL is not set
API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
ADMIN_KEY = os.getenv("ADMIN_KEY", "supersecretkey")
HEADERS = {"X-ADMIN-KEY": ADMIN_KEY}

# --- API Functions ---
@st.cache_data(ttl=60)
def get_prediction(symbol: str):
    """Fetches prediction data for a given stock symbol."""
    if not symbol:
        return None
    try:
        url = f"{API_BASE_URL}/predict"
        params = {"symbol": symbol}
        response = httpx.get(url, headers=HEADERS, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        st.error(f"HTTP error fetching prediction: {e.response.status_code} - {e.response.text}")
    except httpx.RequestError as e:
        st.error(f"Connection error fetching prediction: {e}")
    return None

@st.cache_data(ttl=60)
def get_export_context(symbol: str, format: str = "md"):
    """Fetches the AI hand-off context."""
    if not symbol:
        return None
    try:
        url = f"{API_BASE_URL}/export/context"
        params = {"symbol": symbol, "format": format}
        response = httpx.get(url, headers=HEADERS, params=params, timeout=10)
        response.raise_for_status()
        if format == "json":
            return json.dumps(response.json(), indent=2)
        return response.text
    except httpx.HTTPStatusError as e:
        st.error(f"HTTP error fetching context: {e.response.status_code} - {e.response.text}")
    except httpx.RequestError as e:
        st.error(f"Connection error fetching context: {e}")
    return None

@st.cache_data(ttl=60)
def get_export_pack(symbol: str):
    """Fetches the full data pack."""
    if not symbol:
        return None
    try:
        url = f"{API_BASE_URL}/export/pack"
        params = {"symbol": symbol}
        response = httpx.get(url, headers=HEADERS, params=params, timeout=30)
        response.raise_for_status()
        return response.content
    except httpx.HTTPStatusError as e:
        st.error(f"HTTP error fetching data pack: {e.response.status_code} - {e.response.text}")
    except httpx.RequestError as e:
        st.error(f"Connection error fetching data pack: {e}")
    return None

# --- UI Layout ---
st.set_page_config(layout="wide")
st.title("📈 Stock Hunter AI - Overview")

# --- Symbol Input ---
symbol = st.text_input("Enter a stock symbol to analyze:", "VN30F1M", max_chars=10).upper()

if symbol:
    data = get_prediction(symbol)

    if data:
        # --- Safety Banner ---
        safety_banner = data.get("safety_banner", {})
        level = safety_banner.get("level", "info")
        title = safety_banner.get("title", "Status")
        message = safety_banner.get("message", "System is operating normally.")

        if level == "error":
            st.error(f"**{title}**: {message}")
        elif level == "warning":
            st.warning(f"**{title}**: {message}")
        else:
            st.info(f"**{title}**: {message}")

        # --- Main Metrics ---
        st.header(f"Analysis for {symbol}")
        col1, col2, col3 = st.columns(3)
        col1.metric("🏹 HunterScore", f"{data.get('HunterScore', 'N/A')}")
        col2.metric("🌡️ FrothScore", f"{data.get('FrothScore', 'N/A')}")
        col3.metric("📈 Regime", f"{data.get('regime', 'N/A')}")

        with st.expander("Plain Language Explanation", expanded=True):
            st.markdown(data.get('plain_explainer', 'No explanation available.'))

        # --- AI Hand-off Panel (Sidebar) ---
        st.sidebar.header("🔬 AI Hand-off Panel")
        st.sidebar.info(f"Context for **{symbol}**")

        md_context = get_export_context(symbol, format="md")
        if md_context:
            st.sidebar.text_area("Markdown for Gemini/ChatGPT", md_context, height=200)

        pack_data = get_export_pack(symbol)
        if pack_data:
            st.sidebar.download_button(
                label="📥 Download Full Data Pack (.zip)",
                data=pack_data,
                file_name=f"ai_pack_{symbol}.zip",
                mime="application/zip"
            )
    else:
        st.warning("Could not retrieve data. Is the API running and the symbol correct?")
else:
    st.info("Please enter a stock symbol to begin.")
