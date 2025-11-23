import streamlit as st
import httpx
import os
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt

from datetime import datetime, timedelta

# --- Configuration ---
API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
ADMIN_KEY = os.getenv("ADMIN_KEY", "supersecretkey")
HEADERS = {"X-ADMIN-KEY": ADMIN_KEY}

# --- API Function ---
@st.cache_data(ttl=300)  # Cache for 5 minutes
def get_macro_stats():
    """Fetches macro and sector health statistics."""
    try:
        url = f"{API_BASE_URL}/admin/health/macro_stats"
        response = httpx.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        st.error(f"HTTP error fetching health stats: {e.response.status_code} - {e.response.text}")
    except httpx.RequestError as e:
        st.error(f"Connection error fetching health stats: {e}")
    return None

# --- UI Layout ---
st.set_page_config(layout="wide")
st.title("🌐 System Health & Macro Dashboard")

health_data = get_macro_stats()

if health_data:
    # --- Macro Mini-Hub ---
    st.header("Vĩ mô Mini-Hub")
    macro_hub = health_data.get("macro_mini_hub", {})
    if macro_hub:
        cols = st.columns(len(macro_hub))
        for i, (metric, data) in enumerate(macro_hub.items()):
            # Handle both old format (value only) and new format (dict) for backward compatibility
            if isinstance(data, dict):
                value = data.get('value')
                last_updated_str = data.get('last_updated')
                
                # Display metric
                cols[i].metric(
                    label=metric.replace('_', ' ').title(), 
                    value=f"{value:.2f}" if isinstance(value, (int, float)) else str(value)
                )
                
                # Check staleness
                if last_updated_str:
                    try:
                        last_updated = datetime.fromisoformat(last_updated_str)
                        # Convert to offset-naive if needed for comparison, or ensure both are aware
                        if last_updated.tzinfo:
                            now = datetime.now(last_updated.tzinfo)
                        else:
                            now = datetime.now()
                            
                        age = now - last_updated
                        if age > timedelta(hours=24):
                            cols[i].warning(f"⚠️ {age.days}d old")
                        else:
                            cols[i].caption(f"Updated: {last_updated.strftime('%H:%M')}")
                    except ValueError:
                        pass
            else:
                # Fallback for old format
                cols[i].metric(label=metric.replace('_', ' ').title(), value=f"{data:.2f}")
    else:
        st.info("No Macro Mini-Hub data available.")

    # --- Admin Edit Section ---
    with st.expander("✏️ Edit Macro Data (Admin Only)"):
        st.caption("Manually update monthly/quarterly metrics that are not available via free APIs.")
        
        with st.form("macro_edit_form"):
            # Create inputs for key metrics
            col1, col2 = st.columns(2)
            
            # Helper to get current value safely
            def get_current(metric_name):
                if macro_hub and metric_name in macro_hub:
                    data = macro_hub[metric_name]
                    if isinstance(data, dict):
                        return data.get('value', 0.0)
                    return data
                return 0.0

            with col1:
                cpi = st.number_input("CPI YoY (%)", value=float(get_current('CPI_YOY')))
                gdp = st.number_input("GDP YoY (%)", value=float(get_current('GDP_YOY')))
                policy = st.number_input("Policy Rate (%)", value=float(get_current('POLICY_RATE')))
                credit = st.number_input("Credit Growth (%)", value=float(get_current('CREDIT_GROWTH')))
            
            with col2:
                inflation = st.number_input("Inflation Rate (%)", value=float(get_current('INFLATION_RATE')))
                gdp_growth = st.number_input("GDP Growth (%)", value=float(get_current('GDP_GROWTH')))
                interest = st.number_input("Interest Rate (%)", value=float(get_current('INTEREST_RATE')))
            
            submitted = st.form_submit_button("Update Data")
            
            if submitted:
                update_payload = {
                    "metrics": {
                        "CPI_YOY": cpi,
                        "GDP_YOY": gdp,
                        "POLICY_RATE": policy,
                        "CREDIT_GROWTH": credit,
                        "INFLATION_RATE": inflation,
                        "GDP_GROWTH": gdp_growth,
                        "INTEREST_RATE": interest
                    }
                }
                
                try:
                    update_url = f"{API_BASE_URL}/admin/macro_update"
                    resp = httpx.post(update_url, json=update_payload, headers=HEADERS, timeout=10)
                    resp.raise_for_status()
                    st.success("✅ Data updated successfully! Refresh the page to see changes.")
                    st.cache_data.clear() # Clear cache to show new data immediately
                except Exception as e:
                    st.error(f"❌ Update failed: {e}")

    # --- Sector Rotation RRG (Relative Rotation Graph) ---
    st.header("Sector Rotation (RRG)")
    
    # Fetch sector stats with RRG data
    try:
        url = f"{API_BASE_URL}/admin/health/sector_rotation"
        response = httpx.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        sector_rrg_data = response.json()
    except httpx.HTTPStatusError as e:
        st.error(f"HTTP error fetching RRG data: {e.response.status_code}")
        sector_rrg_data = []
    except httpx.RequestError as e:
        st.error(f"Connection error fetching RRG data: {e}")
        sector_rrg_data = []
    except Exception as e:
        st.error(f"Error fetching RRG data: {e}")
        sector_rrg_data = []
    
    if sector_rrg_data:
        df = pd.DataFrame(sector_rrg_data)
        
        if not df.empty and all(col in df.columns for col in ['sector', 'rs_ratio', 'rs_momentum', 'super_sector']):
            try:
                import plotly.express as px
                
                # Create RRG Scatter Plot
                fig = px.scatter(
                    df,
                    x='rs_ratio',
                    y='rs_momentum',
                    color='super_sector',
                    text='sector',
                    title='Relative Rotation Graph (RRG) - Sector Money Flow',
                    labels={
                        'rs_ratio': 'RS-Ratio (100 = Benchmark)',
                        'rs_momentum': 'RS-Momentum (0 = No Change)',
                        'super_sector': 'Super Sector'
                    },
                    hover_data=['sector', 'super_sector', 'rs_ratio', 'rs_momentum']
                )
                
                # Add quadrant lines
                fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
                fig.add_vline(x=100, line_dash="dash", line_color="gray", opacity=0.5)
                
                # Add quadrant labels
                fig.add_annotation(x=110, y=5, text="<b>LEADING</b><br>Strong + Rising", showarrow=False, font=dict(size=10, color="green"))
                fig.add_annotation(x=110, y=-5, text="<b>WEAKENING</b><br>Strong but Falling", showarrow=False, font=dict(size=10, color="orange"))
                fig.add_annotation(x=90, y=-5, text="<b>LAGGING</b><br>Weak + Falling", showarrow=False, font=dict(size=10, color="red"))
                fig.add_annotation(x=90, y=5, text="<b>IMPROVING</b><br>Weak but Rising", showarrow=False, font=dict(size=10, color="blue"))
                
                # Update layout
                fig.update_traces(textposition='top center', marker=dict(size=12))
                fig.update_layout(
                    height=600,
                    xaxis_title="RS-Ratio (Relative Strength vs VN-Index)",
                    yaxis_title="RS-Momentum (Rate of Change)",
                    showlegend=True
                )
                
                st.plotly_chart(fig, use_container_width=True)
                
                # Add explanation
                with st.expander("ℹ️ How to Read RRG"):
                    st.markdown("""
                    **Relative Rotation Graph (RRG)** hiển thị dòng tiền xoay vòng giữa các ngành:
                    
                    - **Trục X (RS-Ratio)**: Xu hướng dài hạn (60 ngày) so với VN-Index
                        - \u003e 100: Ngành mạnh hơn thị trường
                        - \u003c 100: Ngành yếu hơn thị trường
                    
                    - **Trục Y (RS-Momentum)**: Đà thay đổi ngắn hạn
                        - \u003e 0: Đang tăng tốc
                        - \u003c 0: Đang giảm tốc
                    
                    **4 Góc phần tư**:
                    - 🟢 **Leading**: Ngành dẫn dắt (mạnh + tăng tốc)
                    - 🟠 **Weakening**: Ngành suy yếu (mạnh nhưng giảm tốc)
                    - 🔴 **Lagging**: Ngành tụt hậu (yếu + giảm tốc)
                    - 🔵 **Improving**: Ngành cải thiện (yếu nhưng tăng tốc)
                    
                    **Màu sắc**: Nhóm theo "4 Trụ cột + 1" (Financials, Real Estate Chain, Production & Export, Consumer & Tech, Utilities)
                    """)
            except Exception as e:
                st.error(f"Could not generate RRG chart: {e}")
                st.dataframe(df)
        else:
            st.warning("RRG data is not in the expected format.")
            if not df.empty:
                st.dataframe(df)
    else:
        st.info("No Sector Rotation data available. Run the feature engineering job to populate.")

else:
    st.warning("Could not retrieve system health data from the API.")
