import streamlit as st
import httpx
import json
from datetime import date
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

def get_predator_analysis(symbol: str):
    """Fetches Apex Predator analysis."""
    try:
        with get_api_client() as client:
            response = client.post("/analyze/predator", json={"symbol": symbol}, timeout=60.0)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        st.error(f"Predator Analysis failed: {e}")
    return None

def save_user_prediction(symbol: str, prediction: str):
    """Saves user prediction to journal."""
    try:
        with get_api_client() as client:
            response = client.post("/predator/journal/save", json={"symbol": symbol, "user_prediction": prediction})
            response.raise_for_status()
            return True
    except Exception as e:
        st.error(f"Failed to save prediction: {e}")
        return False

def get_deja_vu(symbol: str):
    """Fetches Deja Vu similar days."""
    try:
        with get_api_client() as client:
            response = client.get(f"/deja_vu/{symbol}")
            response.raise_for_status()
            return response.json()
    except Exception as e:
        st.error(f"Deja Vu failed: {e}")
    return []

def get_simulation(symbol: str, overrides: dict):
    """Runs What-If Simulation."""
    try:
        with get_api_client() as client:
            response = client.post("/analyze/simulate", json={"symbol": symbol, "overrides": overrides})
            response.raise_for_status()
            return response.json()
    except Exception as e:
        st.error(f"Simulation failed: {e}")
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
            
        # --- Apex Predator Training Lab (Story 6.3) ---
        st.divider()
        st.markdown("### 🎯 Apex Predator Training Lab")
        
        # Session State for Reveal
        reveal_key = f"analysis_revealed_{symbol}_{date.today()}"
        if reveal_key not in st.session_state:
            st.session_state[reveal_key] = False
            
        if not st.session_state[reveal_key]:
            # LOCKED VIEW
            st.info("🔒 **Hồ Sơ Mật**: Phân tích kịch bản thị trường & Dòng tiền thông minh.")
            st.markdown("Để xem phân tích của AI Predator (Trưởng Ban Tự Doanh), bạn phải đưa ra nhận định trước.")
            
            user_pred = st.text_area("📝 Nhận định của bạn (Kịch bản là gì? Gom hay Xả?):", 
                                     height=100,
                                     key=f"pred_{symbol}",
                                     help="Ví dụ: Tôi nghĩ đây là giai đoạn gom hàng vì vol thấp...")
            
            # Anti-cheat: Min 20 chars
            can_unlock = len(user_pred.strip()) >= 20
            
            if st.button("🔓 Mở Hồ Sơ Mật", disabled=not can_unlock):
                if save_user_prediction(symbol, user_pred):
                    st.session_state[reveal_key] = True
                    st.rerun()
        else:
            # REVEALED VIEW
            with st.spinner("Đang giải mã hồ sơ..."):
                ai_data = get_predator_analysis(symbol)
            
            if ai_data:
                analysis = ai_data.get('ai_analysis', {})
                
                # Comparison Layout
                c1, c2 = st.columns(2)
                with c1:
                    st.markdown("#### 👤 Nhận định của bạn")
                    st.info(st.session_state.get(f"pred_{symbol}", "Đã lưu vào Nhật ký."))
                    
                with c2:
                    st.markdown("#### 🤖 AI Predator")
                    st.markdown(f"**Kịch bản:** `{ai_data.get('scenario_tag')}`")
                    st.markdown(f"**Vùng kẹp:** `{ai_data.get('trapped_price'):,.0f}`")
                
                # Memo
                st.markdown("---")
                st.markdown("### 📋 Memo Nội Bộ")
                st.error(f"**TO: TRADING DESK**\n\n{analysis.get('predator_memo', 'N/A')}")
                
                with st.expander("📊 Giải thích Kỹ thuật (Quant)", expanded=True):
                    st.markdown(analysis.get('quant_explanation', 'N/A'))
                    
                with st.expander("🛡️ Kế hoạch Hành động (Playbook)"):
                    st.success(analysis.get('user_playbook', 'N/A'))

        # --- Deja Vu Engine (Story 6.4) ---
        st.divider()
        st.markdown("### 🕰️ Deja Vu Engine")
        
        if st.button("🔍 Tìm kiếm Quá khứ (Deja Vu)"):
            with st.spinner("Scanning historical patterns..."):
                similar_days = get_deja_vu(symbol)
                
            if similar_days:
                st.success(f"Found {len(similar_days)} similar historical patterns.")
                
                cols = st.columns(len(similar_days))
                for i, day in enumerate(similar_days):
                    with cols[i]:
                        st.markdown(f"**{day['date']}**")
                        st.caption(f"Similarity: {day['similarity_score']:.2%}")
                        st.markdown(f"Context: `{day['scenario_context']}`")
                        
                        ret = day['return_t5']
                        color = "green" if ret > 0 else "red"
                        st.markdown(f"T+5 Return: :{color}[{ret:.1%}]")
            else:
                st.info("No similar patterns found (or insufficient history).")

        # --- What-If Simulator (Story 6.5) ---
        st.divider()
        st.markdown("### 🧪 What-If Simulator")
        st.caption("Giả lập thay đổi tham số để xem kịch bản thị trường thay đổi như thế nào.")
        
        with st.expander("Open Simulator"):
            sim_col1, sim_col2 = st.columns(2)
            with sim_col1:
                sim_vol = st.slider("Volume Relative (vs 20d avg)", 0.0, 5.0, 1.0, 0.1)
                sim_price = st.slider("Price Change (%)", -10.0, 10.0, 0.0, 0.5) / 100.0
            with sim_col2:
                sim_crowd = st.slider("Crowd Hype (Z-Score)", -3.0, 3.0, 0.0, 0.1)
                sim_elite = st.slider("Elite Hype (Z-Score)", -3.0, 3.0, 0.0, 0.1)
                
            if st.button("Run Simulation"):
                overrides = {
                    "vol_rel": sim_vol,
                    "price_change": sim_price,
                    "hype_crowd_z": sim_crowd,
                    "hype_elitist_z": sim_elite
                }
                sim_result = get_simulation(symbol, overrides)
                
                if sim_result:
                    st.markdown(f"**Simulated Scenario:** `{sim_result.get('scenario_tag')}`")
                    
                    # Visual feedback
                    tag = sim_result.get('scenario_tag')
                    if tag == "DISTRIBUTION_CLIMAX":
                        st.error("⚠️ DANGER: Distribution Detected!")
                    elif tag == "STEALTH_ACCUMULATION":
                        st.success("✅ OPPORTUNITY: Stealth Accumulation!")
                    elif tag == "SHAKEOUT":
                        st.warning("🌪️ CAUTION: Shakeout!")
                    elif tag == "UPTHRUST":
                        st.error("⛔ WARNING: Upthrust (Trap)!")
                    else:
                        st.info("Neutral / Unclear")

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
