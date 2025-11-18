# src/dash/pages/02_Active_Learning.py
import streamlit as st
import httpx
import uuid
from datetime import datetime
import pandas as pd
import plotly.graph_objects as go
from utils import get_api_client

# --- API Functions ---

@st.cache_data(ttl=60 * 5) # Cache for 5 minutes
def get_al_queue():
    """Fetches the Active Learning queue from the real API."""
    try:
        with get_api_client() as client:
            response = client.get("/al/queue")
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        st.error(f"HTTP error fetching AL queue: {e.response.status_code} - {e.response.text}")
    except httpx.RequestError as e:
        st.error(f"Connection error fetching AL queue: {e}")
    return [] # Return empty list on failure

def post_al_label(task_id: str, action: str, is_sandbox: bool):
    """Posts a label to the real API."""
    idempotency_key = str(uuid.uuid4())
    payload = {
        "task_id": task_id,
        "action": action, # "confirm", "flip", "snooze"
        "is_sandbox": is_sandbox,
        "idempotency_key": idempotency_key,
    }
    try:
        with get_api_client() as client:
            response = client.post("/al/label", json=payload)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        st.error(f"HTTP error posting label: {e.response.status_code} - {e.response.text}")
    except httpx.RequestError as e:
        st.error(f"Connection error posting label: {e}")
    return None

# --- UI Implementation ---

st.title("👩‍🏫 Active Learning Review")

# --- Header & Controls ---
c1, c2, c3 = st.columns((1, 2, 1))
is_sandbox = c1.toggle("Sandbox Mode", value=True, help="In Sandbox Mode, your labels help you learn but do not affect the live model.")
# TODO: Replace placeholders with real data if available from a future API endpoint
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
    if not st.session_state.al_queue or st.session_state.al_idx >= len(st.session_state.al_queue):
        return

    current_sample = st.session_state.al_queue[st.session_state.al_idx]
    task_id = current_sample.get("task_id")

    if not task_id:
        st.error("Invalid sample data: missing 'task_id'.")
        return

    response = post_al_label(task_id, action, is_sandbox)

    if response and response.get("status") == "success":
        st.toast("✅ Label recorded successfully!")
        if st.session_state.al_idx < len(st.session_state.al_queue) - 1:
            st.session_state.al_idx += 1
        else:
            st.session_state.al_queue = [] # Mark as done
    else:
        st.toast("❌ Error submitting label.", icon="🔥")
    st.rerun()


# Check if there are items left to review
if not st.session_state.al_queue or st.session_state.al_idx >= len(st.session_state.al_queue):
    st.success("🎉 You've reviewed all samples in the queue. Please check back next week!")
    st.stop()

# Get the current sample to display
sample = st.session_state.al_queue[st.session_state.al_idx]
try:
    effective_date = datetime.strptime(sample['effective_date'], '%Y-%m-%d').date()
except (KeyError, TypeError, ValueError):
    st.error(f"Invalid sample data: could not parse 'effective_date' for task. Got: {sample.get('effective_date')}")
    st.stop()

with st.container(border=True):
    st.subheader(f"Symbol: `{sample.get('symbol', 'N/A')}`")
    st.caption(f"Effective Date: `{sample.get('effective_date')}` | Reason: `{sample.get('reason', 'N/A')}`")

    with st.expander("Show Evidence (Charts & Data)"):
        price_history = sample.get('price_history', [])
        if not price_history or not isinstance(price_history, list):
            st.warning("No price history available for this sample.")
        else:
            price_df = pd.DataFrame(price_history)
            price_df['date'] = pd.to_datetime(price_df['date']).dt.date

            # *** CRITICAL: THE AS-OF GUARD ***
            evidence_df = price_df[price_df['date'] <= effective_date].copy()

            if evidence_df.empty:
                st.warning("No price history available up to the effective date.")
            else:
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=evidence_df['date'], y=evidence_df['close'], mode='lines', name='Close Price'))
                fig.add_vline(x=effective_date, line_width=2, line_dash="dash", line_color="red", annotation_text="Effective Date")
                fig.update_layout(
                    title=f"Price History for {sample.get('symbol')} (Data shown *only* up to {sample.get('effective_date')})",
                    xaxis_title="Date",
                    yaxis_title="Price"
                )
                st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")
    st.info(f"**System Suggestion:** `{sample.get('suggested_label', 'N/A')}`")

    # --- Action Buttons ---
    b1, b2, b3, _ = st.columns([1.5, 1.5, 1.5, 5])
    task_key = sample.get('task_id', uuid.uuid4())
    b1.button("Confirm 👍 (C)", on_click=handle_action, args=("confirm",), key=f"c_{task_key}", use_container_width=True)
    b2.button("Flip 👎 (F)", on_click=handle_action, args=("flip",), key=f"f_{task_key}", use_container_width=True)
    b3.button("Snooze 😴 (S)", on_click=handle_action, args=("snooze",), key=f"s_{task_key}", use_container_width=True)
