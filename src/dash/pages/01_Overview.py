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
            # API returns JSON with "context_md" or "context_json" field
            result = response.json()
            if format == "json":
                return json.dumps(result.get("context_json", result), indent=2)
            else:
                # Extract the markdown content from the JSON response
                return result.get("context_md", "")
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

def get_ai_analysis(symbol: str):
    """Fetches deep AI analysis for a symbol."""
    try:
        with get_api_client() as client:
            response = client.post("/analyze/ai", json={"symbol": symbol}, timeout=30.0) # Longer timeout for LLM
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        st.error(f"AI Analysis failed: {e.response.status_code} - {e.response.text}")
    except httpx.RequestError as e:
        st.error(f"Connection error during AI analysis: {e}")
    return None

def submit_sts_feedback(symbol: str, score: int, comment: str):
    """Submits Signal Trust Score feedback."""
    try:
        with get_api_client() as client:
            # Using event_log for now as per schema
            payload = {
                "event_name": "sts_feedback",
                "actor": "user", # In real app, get from auth
                "meta": {
                    "symbol": symbol,
                    "score": score,
                    "comment": comment
                }
            }
            # We don't have a direct endpoint for this in the original plan, 
            # but we can use a generic event logging endpoint if it existed, 
            # or reuse the AL label endpoint with a specific type.
            # For this fix, we'll assume we need to add a feedback endpoint or just log it locally if API is missing.
            # Wait, the plan said "Store feedback in event_log".
            # Let's check if there is an endpoint for generic logging. 
            # The API has `export_pack` which logs events.
            # We should probably add a dedicated endpoint or just print for now if we can't modify API again.
            # BUT, I am in EXECUTION mode and I can modify API if needed.
            # However, to keep it simple and "Lean", I will add a simple endpoint to API in the next step if needed.
            # For now, let's assume we will add `POST /feedback` to API.
            response = client.post("/feedback", json=payload)
            response.raise_for_status()
            return True
    except Exception as e:
        st.error(f"Failed to submit feedback: {e}")
        return False


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
            
            st.divider()
            st.markdown("### 🧠 Deep Analysis")
            if st.button("✨ Ask AI Advisor"):
                with st.spinner("Consulting AI Advisor (Gemini 3.0)..."):
                    ai_result = get_ai_analysis(symbol)
                    if ai_result:
                        st.markdown(ai_result.get("analysis", "No analysis returned."))
                        st.caption(f"Analysis by {ai_result.get('model', 'unknown model')}")

        # --- Signal Trust Score (STS) Survey (FR14) ---
        st.divider()
        st.subheader("🗳️ Signal Trust Score")
        with st.form("sts_form"):
            st.write("How much do you trust this signal?")
            sts_score = st.slider("Trust Score (1-5)", 1, 5, 3)
            sts_comment = st.text_area("Feedback (Optional)")
            submitted = st.form_submit_button("Submit Feedback")
            
            if submitted:
                if submit_sts_feedback(symbol, sts_score, sts_comment):
                    st.success("Thank you for your feedback!")

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
