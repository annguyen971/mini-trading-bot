import streamlit as st
from utils import get_api_client, HEADERS, API_BASE_URL
from datetime import date
import pandas as pd
import os

st.set_page_config(page_title="Macro Impact Config", layout="wide")

# Auth is handled by utils import

st.title("Bảng điều khiển & Ghi đè Tác động Vĩ mô (Story 5.7)")

@st.cache_data(ttl=60)
def fetch_data():
    """Fetches macro impact configuration data from the API."""
    client = get_api_client()
    response = client.get(f"/admin/macro/impact") # Base URL is already in client
    response.raise_for_status()
    return response.json()

def save_data(data_df):
    """Saves the modified override data back to the API."""
    payload = data_df.to_dict('records')
    for row in payload:
        # Handle NaT dates from pandas
        if pd.isna(row.get('ttl_until')):
            row['ttl_until'] = None
        # Format date to ISO string if it's a date object
        if isinstance(row.get('ttl_until'), (date, pd.Timestamp)):
            row['ttl_until'] = row['ttl_until'].isoformat()

    # Send only the required columns for the update
    clean_payload = [
        {
            "sector": r["sector"],
            "weight": r["weight_override"],
            "ttl_until": r["ttl_until"]
        }
        for r in payload
    ]

    client = get_api_client()
    response = client.post("/admin/macro/impact", json=clean_payload)
    response.raise_for_status()
    return True

try:
    data = fetch_data()
    df = pd.DataFrame(data)

    # Prepare dataframe for display
    df['confidence'] = df['confidence'].fillna(0.0)
    df['confidence_pct'] = (df['confidence'] * 100).astype(int)
    df['ttl_until'] = pd.to_datetime(df['ttl_until']).dt.date

    edited_df = st.data_editor(
        df,
        column_config={
            "sector": st.column_config.TextColumn("Ngành (Sector)", disabled=True),
            "display_name": st.column_config.TextColumn("Tên Ngành", disabled=True),
            "impact_suggested": st.column_config.NumberColumn("Suggested (Auto)", format="%.2f", disabled=True),
            "confidence_pct": st.column_config.ProgressColumn(
                "Confidence (Auto)",
                format="%d%%",
                min_value=0,
                max_value=100,
                disabled=True
            ),
            "weight_override": st.column_config.NumberColumn(
                "Override (Manual)",
                format="%.2f",
                min_value=-1.0,
                max_value=1.0,
                help="Set a manual override for the macro impact. This value will be used instead of the suggested one."
            ),
            "ttl_until": st.column_config.DateColumn(
                "Override TTL (Manual)",
                help="The override will be active until this date. Leave blank for it to be active indefinitely."
            ),
            # Hide the raw confidence column
            "confidence": None,
        },
        hide_index=True,
    )

    if st.button("Lưu Thay đổi (Ghi đè UI)"):
        with st.spinner("Đang lưu..."):
            try:
                save_data(edited_df)
                st.success("Đã lưu các giá trị ghi đè (override)!")
                # Clear cache to force a reload of data on next run
                st.cache_data.clear()
                st.rerun()
            except Exception as e:
                st.error(f"Lỗi khi lưu: {e}")

except Exception as e:
    st.error(f"Lỗi tải dữ liệu: {e}")
