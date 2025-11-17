# src/dash/pages/02_Active_Learning.py
import streamlit as st
import httpx
import uuid
from datetime import datetime, timedelta
import pandas as pd
import plotly.graph_objects as go

# Assuming app.py is in the parent directory and contains check_auth
# For development, we can create a placeholder app.py if it doesn't exist
from app import check_auth

# --- Authentication ---
if not check_auth():
    st.error("🔒 Please authenticate to access this page.")
    st.stop()

# --- Mock API Functions (as per Session 3 instructions) ---

def get_al_queue():
    """
    Mock function to simulate fetching the Active Learning queue from the API.
    Returns a list of sample dictionaries.
    """
    # Create a more realistic price history for the chart
    base_date = datetime.utcnow().date()
    price_data = {
        'date': [base_date - timedelta(days=i) for i in range(60)][::-1],
        'close': [100 + i + (-1)**i * 5 for i in range(60)]
    }
    price_df = pd.DataFrame(price_data)

    return [
        {
            "task_id": "al_task_001",
            "symbol": "ABC",
            "effective_date": (base_date - timedelta(days=15)).strftime('%Y-%m-%d'),
            "reason": "High Entropy",
            "suggested_label": "TÍCH LŨY",
            "price_history": price_df.to_dict('records') # More realistic data
        },
        {
            "task_id": "al_task_002",
            "symbol": "XYZ",
            "effective_date": (base_date - timedelta(days=10)).strftime('%Y-%m-%d'),
            "reason": "Model Disagreement",
            "suggested_label": "PHÂN PHỐI",
            "price_history": price_df.to_dict('records')
        },
    ]

def post_al_label(task_id: str, action: str, is_sandbox: bool):
    """
    Mock function to simulate posting a label to the API.
    """
    idempotency_key = str(uuid.uuid4())
    payload = {
        "task_id": task_id,
        "action": action, # "confirm", "flip", "snooze"
        "is_sandbox": is_sandbox,
        "idempotency_key": idempotency_key,
    }
    # In a real app, you'd use httpx.post(...)
    print(f"MOCK API CALL: post_al_label with payload: {payload}")
    # Simulate success
    return {"status": "success", "message": "Label recorded"}


# --- UI Implementation ---

st.title("👩‍🏫 Active Learning Review")

# --- Header & Controls ---
c1, c2, c3 = st.columns((1, 2, 1))
is_sandbox = c1.toggle("Sandbox Mode", value=True, help="In Sandbox Mode, your labels help you learn but do not affect the live model.")
# Placeholder for progress and quality
c2.progress(0, "0/10 Reviewed This Week")
c3.metric("Review Quality", "On Track", help="Based on honeypot sample accuracy.")

st.info("💡 **Your task:** Review the system's suggested label based on the evidence available *only up to the effective date*. Use the buttons to agree, disagree, or skip.", icon="ℹ️")

# --- Main AL Loop ---

# Initialize session state for the queue and index
if 'al_queue' not in st.session_state:
    st.session_state.al_queue = get_al_queue()
    st.session_state.al_idx = 0

def handle_action(action: str):
    """Callback to handle button clicks."""
    current_sample = st.session_state.al_queue[st.session_state.al_idx]
    task_id = current_sample["task_id"]

    # (AC6) Call the API
    response = post_al_label(task_id, action, is_sandbox)

    if response and response.get("status") == "success":
        # (AC5) Neutral Feedback
        st.toast("✅ Đã ghi nhận!")
        # Advance to the next sample
        if st.session_state.al_idx < len(st.session_state.al_queue) - 1:
            st.session_state.al_idx += 1
        else:
            # Mark the queue as done
            st.session_state.al_queue = []
    else:
        st.toast("❌ Error submitting label.", icon="🔥")


# Check if there are items left to review
if not st.session_state.al_queue or st.session_state.al_idx >= len(st.session_state.al_queue):
    st.success("🎉 You've reviewed all samples in the queue. Please check back next week!")
    st.stop()

# Get the current sample to display
sample = st.session_state.al_queue[st.session_state.al_idx]
effective_date = datetime.strptime(sample['effective_date'], '%Y-%m-%d').date()


with st.container(border=True):
    st.subheader(f"Symbol: `{sample['symbol']}`")
    st.caption(f"Effective Date: `{sample['effective_date']}` | Reason for Review: `{sample['reason']}`")

    with st.expander("Show Evidence (Charts & Data)"):
        # (AC3 - As-of Guard)
        price_df = pd.DataFrame(sample['price_history'])
        price_df['date'] = pd.to_datetime(price_df['date']).dt.date

        # *** THE AS-OF GUARD ***
        evidence_df = price_df[price_df['date'] <= effective_date]

        if evidence_df.empty:
            st.warning("No price history available up to the effective date.")
        else:
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=evidence_df['date'], y=evidence_df['close'], mode='lines', name='Close Price'))
            # Convert effective_date to a pandas Timestamp to ensure Plotly compatibility
            #fig.add_vline(x=pd.Timestamp(effective_date), line_width=2, line_dash="dash", line_color="red", annotation_text="Effective Date")
            # --- FIX PANDAS 2.0 vs PLOTLY ---
            # Chuyển đổi timestamp sang milliseconds (int64)
            try:
                vline_x = pd.Timestamp(effective_date).value // 10**6
            except:
                vline_x = effective_date # Fallback nếu lỗi

            fig.add_vline(x=vline_x, line_width=2, line_dash="dash", line_color="red", annotation_text="Effective Date")
            # --------------------------------
            fig.update_layout(
                title=f"Price History for {sample['symbol']} (Data shown only up to {sample['effective_date']})",
                xaxis_title="Date",
                yaxis_title="Price",
                legend_title="Series"
            )
            st.plotly_chart(fig, use_container_width=True)


    st.markdown("---")
    st.info(f"**System Suggestion:** `{sample['suggested_label']}`")

    # --- Action Buttons ---
    b1, b2, b3, b4 = st.columns([1.5, 1.5, 1.5, 5])

    # (AC4 - Hotkeys)
    b1.button("Confirm 👍", key=f"confirm_{sample['task_id']}", on_click=handle_action, args=("confirm",), help="Agree with the suggestion.\n\n*Phím tắt: C*", use_container_width=True)
    b2.button("Flip 👎", key=f"flip_{sample['task_id']}", on_click=handle_action, args=("flip",), help="Disagree with the suggestion.\n\n*Phím tắt: F*", use_container_width=True)
    b3.button("Snooze 😴", key=f"snooze_{sample['task_id']}", on_click=handle_action, args=("snooze",), help="Skip this sample for now.\n\n*Phím tắt: S*", use_container_width=True)

st.caption("Use `C`, `F`, `S` hotkeys for faster review (feature coming soon).")
