import streamlit as st
import httpx
import json
from utils import get_api_client, show_safety_banner

# --- API Functions ---

@st.cache_data(ttl=60)
def get_prediction(symbol: str):
    """Fetches prediction data for a given stock symbol."""
    if not symbol:
        return None
    try:
        with get_api_client() as client:
            response = client.get("/predict", params={"symbol": symbol})
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        # Specifically handle 503 Service Unavailable (likely Kill-Switch)
        if e.response.status_code == 503:
            st.error("🚨 **Service Unavailable**: The prediction service is currently offline. This might be due to a Kill-Switch or maintenance. Please try again later.")
        else:
            st.error(f"HTTP error fetching prediction: {e.response.status_code} - {e.response.text}")
    except httpx.RequestError as e:
        st.error(f"Connection error fetching prediction: {e}")
    return None

@st.cache_data(ttl=300) # Cache longer as this data is less likely to change frequently
def get_export_context(symbol: str, format: str = "md"):
    """Fetches the AI hand-off context."""
    if not symbol:
        return None
    try:
        with get_api_client() as client:
            response = client.get("/export/context", params={"symbol": symbol, "format": format})
            response.raise_for_status()
            if format == "json":
                return json.dumps(response.json(), indent=2)
            return response.text
    except httpx.HTTPStatusError as e:
        st.error(f"HTTP error fetching context: {e.response.status_code} - {e.response.text}")
    except httpx.RequestError as e:
        st.error(f"Connection error fetching context: {e}")
    return None

@st.cache_data(ttl=300)
def get_export_pack(symbol: str):
    """Fetches the full data pack."""
    if not symbol:
        return None
    try:
        with get_api_client() as client:
            response = client.get("/export/pack", params={"symbol": symbol})
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
        show_safety_banner(data.get("safety_banner", {}))

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
            st.sidebar.download_button(
                label="📋 Copy Markdown Prompt",
                data=md_context,
                file_name=f"prompt_{symbol}.md",
                mime="text/markdown",
            )

        pack_data = get_export_pack(symbol)
        if pack_data:
            st.sidebar.download_button(
                label="📥 Download Full Data Pack (.zip)",
                data=pack_data,
                file_name=f"ai_pack_{symbol}.zip",
                mime="application/zip",
            )
    else:
        st.warning("Could not retrieve data. Is the API running and the symbol correct?")
else:
    st.info("Please enter a stock symbol to begin.")
