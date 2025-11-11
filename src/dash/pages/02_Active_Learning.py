# src/dash/pages/02_Active_Learning.py
import streamlit as st
import os
import sys
import httpx
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime

# Add the root directory to the Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app import check_auth

# --- API Configuration ---
API_BASE_URL = os.getenv("API_BASE_URL", "http://api:8000")
ADMIN_KEY = os.getenv("ADMIN_KEY")
HEADERS = {"X-ADMIN-KEY": ADMIN_KEY}

# --- State Management ---
def initialize_state():
    if 'al_queue' not in st.session_state:
        st.session_state.al_queue = []
    if 'al_current_index' not in st.session_state:
        st.session_state.al_current_index = 0

# --- API Functions ---
@st.cache_data(ttl=300)
def get_al_queue_from_api():
    try:
        url = f"{API_BASE_URL}/al/queue"
        response = httpx.get(url, headers=HEADERS, timeout=10.0)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        st.error(f"Failed to fetch AL queue: {e}")
    return []

@st.cache_data(ttl=300)
def get_price_data_for_sample(symbol, effective_date):
    try:
        url = f"{API_BASE_URL}/data/price?symbol={symbol}&end_date={effective_date}"
        response = httpx.get(url, headers=HEADERS, timeout=10.0)
        response.raise_for_status()
        data = response.json()
        if not data:
            return pd.DataFrame()
        price_df = pd.DataFrame(data)
        price_df['date'] = pd.to_datetime(price_df['date'])
        return price_df
    except Exception as e:
        st.error(f"Failed to fetch price data: {e}")
    return pd.DataFrame()

def post_label_to_api(symbol, effective_date, old_label, new_label, is_sandbox):
    try:
        url = f"{API_BASE_URL}/al/label"
        payload = {
            "symbol": symbol,
            "effective_date": effective_date.isoformat(),
            "old_label": old_label,
            "new_label": new_label,
            "is_sandbox": is_sandbox,
            "idempotency_key": f"{symbol}-{effective_date.isoformat()}-{datetime.now().timestamp()}"
        }
        response = httpx.post(url, headers=HEADERS, json=payload, timeout=10.0)
        response.raise_for_status()
        return True
    except Exception as e:
        st.error(f"Failed to submit label: {e}")
    return False

def advance_queue():
    st.session_state.al_current_index += 1

# --- UI Components ---
def display_sample_card(sample):
    st.header(f"Reviewing: `{sample['symbol']}` on `{sample['effective_date']}`")
    st.info(f"Reason: **{sample.get('reason', 'N/A').capitalize()}**")

    price_df = get_price_data_for_sample(sample['symbol'], sample['effective_date'])

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=price_df['date'], y=price_df['price'], mode='lines', name='Price'))
    fig.add_vline(x=pd.to_datetime(sample['effective_date']), line_width=2, line_dash="dash", line_color="red", annotation_text="Effective Date")
    fig.update_layout(title="Price Chart (As-of Guard Applied)")
    st.plotly_chart(fig, use_container_width=True)

    suggested_label = 1 # Mock
    st.write(f"System's suggested label: **{suggested_label}**")

    return suggested_label

# --- Main Page ---
def main():
    st.set_page_config(layout="wide", page_title="Stock Hunter AI - Active Learning")
    check_auth()
    initialize_state()

    st.title("Active Learning Review")

    col1, _, col3 = st.columns([1, 2, 1])
    is_sandbox = col1.toggle("Sandbox Mode", value=True)
    if col3.button("Refresh Queue"):
        st.session_state.al_queue = get_al_queue_from_api()
        st.session_state.al_current_index = 0
        st.rerun()

    if not st.session_state.al_queue:
        st.session_state.al_queue = get_al_queue_from_api()

    queue = st.session_state.al_queue
    index = st.session_state.al_current_index

    if not queue or index >= len(queue):
        st.success("🎉 The Active Learning queue is empty or you've finished it. Great job!")
        st.stop()

    st.progress((index + 1) / len(queue), text=f"Item {index + 1} of {len(queue)}")

    current_sample = queue[index]

    with st.container(border=True):
        suggested_label = display_sample_card(current_sample)

        b_col1, b_col2, b_col3 = st.columns(3)
        if b_col1.button("Confirm (C)", use_container_width=True):
            if post_label_to_api(current_sample['symbol'], pd.to_datetime(current_sample['effective_date']), suggested_label, suggested_label, is_sandbox):
                st.toast("Đã ghi nhận!", icon="✅")
                advance_queue()
                st.rerun()

        flipped_label = (suggested_label + 1) % 4
        if b_col2.button("Flip (F)", use_container_width=True):
            if post_label_to_api(current_sample['symbol'], pd.to_datetime(current_sample['effective_date']), suggested_label, flipped_label, is_sandbox):
                st.toast("Đã ghi nhận!", icon="🔄")
                advance_queue()
                st.rerun()

        if b_col3.button("Snooze (S)", use_container_width=True):
            st.toast("Sample snoozed.", icon="😴")
            advance_queue()
            st.rerun()

if __name__ == "__main__":
    main()
