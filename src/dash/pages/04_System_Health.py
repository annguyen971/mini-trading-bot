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
        
        if not df.empty and all(col in df.columns for col in ['sector', 'rs_ratio', 'rs_momentum', 'super_sector', 'as_of_date']):
            try:
                import plotly.graph_objects as go
                
                # 1. Prepare Data
                # Sort by date to ensure trails are drawn correctly
                df['as_of_date'] = pd.to_datetime(df['as_of_date'])
                df = df.sort_values(['sector', 'as_of_date'])
                
                # Get latest data for markers and labels
                latest_date = df['as_of_date'].max()
                df_latest = df[df['as_of_date'] == latest_date].copy()
                
                # 2. Calculate Symmetric Range (Strict Square)
                # Find max deviation from 100 across all data points (including history)
                max_dev_x = max(abs(df['rs_ratio'].max() - 100), abs(df['rs_ratio'].min() - 100))
                max_dev_y = max(abs(df['rs_momentum'].max() - 100), abs(df['rs_momentum'].min() - 100))
                limit = max(max_dev_x, max_dev_y, 10.0) * 1.1 # Min 10.0 deviation, 10% buffer
                
                range_min = 100 - limit
                range_max = 100 + limit
                
                # 3. Create Figure
                fig = go.Figure()
                
                # Define colors for Super Sectors
                super_sectors = df['super_sector'].unique()
                palette = sns.color_palette("husl", len(super_sectors)).as_hex()
                color_map = {sector: color for sector, color in zip(super_sectors, palette)}
                
                # 4. Draw Trails (History)
                for sector in df['sector'].unique():
                    sector_df = df[df['sector'] == sector]
                    if len(sector_df) > 1:
                        super_sec = sector_df['super_sector'].iloc[0]
                        color = color_map.get(super_sec, 'grey')
                        
                        fig.add_trace(go.Scatter(
                            x=sector_df['rs_ratio'],
                            y=sector_df['rs_momentum'],
                            mode='lines',
                            line=dict(color=color, width=1, dash='dot'), # Thinner, dotted lines for trails
                            opacity=0.5,
                            showlegend=False,
                            hoverinfo='skip'
                        ))

                # 5. Draw Markers (Latest)
                for super_sec in super_sectors:
                    sec_df = df_latest[df_latest['super_sector'] == super_sec]
                    color = color_map.get(super_sec, 'grey')
                    
                    fig.add_trace(go.Scatter(
                        x=sec_df['rs_ratio'],
                        y=sec_df['rs_momentum'],
                        mode='markers+text',
                        marker=dict(size=12, color=color),
                        text=sec_df['sector'],
                        textposition='top center',
                        name=super_sec,
                        customdata=sec_df[['sector', 'breadth', 'concentration', 'turnover_shock_z']],
                        hovertemplate="<b>%{text}</b><br>Ratio: %{x:.2f}<br>Mom: %{y:.2f}<br>Breadth: %{customdata[1]:.2f}<br>Conc: %{customdata[2]:.2f}<br>Shock: %{customdata[3]:.2f}<extra></extra>"
                    ))
                    
                    # 6. Smart Annotations (Updated Logic)
                    # TRỤ KÉO (Pillar Pull): Conc > 0.6 & Breadth < 0.3
                    # LAN TỎA THẬT (True Broad Rally): Breadth > 0.7 & Conc < 0.4
                    # LAN TỎA (Broad Rally): Breadth > 0.7 & Mom > 100 (Secondary)
                    # TIỀN VÀO (Money In): Shock > 1.5
                    
                    for idx, row in sec_df.iterrows():
                        alerts = []
                        breadth = row.get('breadth', 0)
                        conc = row.get('concentration', 0)
                        shock = row.get('turnover_shock_z', 0)
                        mom = row['rs_momentum']
                        
                        if conc > 0.6 and breadth < 0.3:
                            alerts.append("TRỤ KÉO")
                        elif breadth > 0.7 and conc < 0.4:
                            alerts.append("LAN TỎA THẬT")
                        elif breadth > 0.7 and mom > 100:
                            alerts.append("LAN TỎA")
                            
                        if shock > 1.5:
                            alerts.append("TIỀN VÀO")
                            
                        if alerts:
                            alert_text = " | ".join(alerts)
                            fig.add_annotation(
                                x=row['rs_ratio'],
                                y=row['rs_momentum'],
                                text=f"<span style='color:red; font-size:10px'><b>{alert_text}</b></span>",
                                showarrow=True,
                                arrowhead=1,
                                yshift=-20 # Shift below the marker
                            )

                # 7. Layout & Quadrants (P0: Strict Square & Center 100)
                fig.update_layout(
                    title='Relative Rotation Graph (RRG) 2.0 - Market X-Ray',
                    height=600,
                    width=600,
                    xaxis=dict(title="RS-Ratio (Trend)", range=[range_min, range_max], constrain='domain', zeroline=False, showgrid=True),
                    yaxis=dict(title="RS-Momentum (Velocity)", range=[range_min, range_max], scaleanchor="x", scaleratio=1, zeroline=False, showgrid=True),
                    showlegend=True,
                    template="plotly_white",
                    shapes=[
                        # Center Lines at 100, 100
                        dict(type="line", x0=100, x1=100, y0=range_min, y1=range_max, line=dict(color="gray", width=2)),
                        dict(type="line", x0=range_min, x1=range_max, y0=100, y1=100, line=dict(color="gray", width=2))
                    ]
                )
                
                # Quadrant Labels (Anchored to 100)
                mid = limit / 2
                fig.add_annotation(x=100+mid, y=100+mid, text="<b>LEADING</b>", showarrow=False, font=dict(color="green", size=14))
                fig.add_annotation(x=100+mid, y=100-mid, text="<b>WEAKENING</b>", showarrow=False, font=dict(color="orange", size=14))
                fig.add_annotation(x=100-mid, y=100-mid, text="<b>LAGGING</b>", showarrow=False, font=dict(color="red", size=14))
                fig.add_annotation(x=100-mid, y=100+mid, text="<b>IMPROVING</b>", showarrow=False, font=dict(color="blue", size=14))

                st.plotly_chart(fig, use_container_width=False)
                
                # Add explanation
                with st.expander("ℹ️ How to Read RRG 2.0"):
                    st.markdown("""
                    **Relative Rotation Graph (RRG) 2.0** với Market X-Ray:
                    
                    - **Trục X (RS-Ratio)**: Xu hướng dài hạn (6 tháng). >100 là Mạnh.
                    - **Trục Y (RS-Momentum)**: Gia tốc ngắn hạn (2 tuần). >100 là Tăng tốc.
                    - **Đuôi (Trails)**: 5 phiên gần nhất, cho thấy hướng di chuyển.
                    
                    **Cảnh báo thông minh (Market X-Ray):**
                    - 🚨 **TRỤ KÉO (Pillar Pull)**: Ngành mạnh nhưng Độ tập trung cao (>60% vol vào Top 3). Xanh vỏ đỏ lòng?
                    - 🌊 **LAN TỎA (Broad Rally)**: Ngành tăng tốc với Độ rộng tốt (>70% mã > SMA20). Tăng bền vững.
                    - 💰 **TIỀN VÀO (Money In)**: Đột biến thanh khoản (Turnover Shock > 1.5 sigma). Dòng tiền lớn tham gia.
                    """)

                # --- Market X-Ray Table (User Request - Final Fix) ---
                st.markdown("---")
                st.subheader("🔬 Market X-Ray: Soi Chiếu Cấu Trúc Ngành")

                # Debug Log
                if 'df_latest' in locals() and not df_latest.empty:
                    st.caption(f"Debug: Loaded {len(df_latest)} rows for Market X-Ray.")
                    
                    # 1. Use Latest Data
                    xray_df = df_latest.copy()
                    
                    # 2. Ensure Columns Exist (Fix lỗi thiếu cột)
                    required_cols = ['concentration', 'breadth', 'turnover_shock_z', 'rs_ratio', 'sector']
                    for col in required_cols:
                        if col not in xray_df.columns:
                            xray_df[col] = 0.0 # Fill default
                            
                    # 3. Diagnose Logic
                    def diagnose(row):
                        conc = float(row.get('concentration', 0) or 0)
                        breadth = float(row.get('breadth', 0) or 0)
                        shock = float(row.get('turnover_shock_z', 0) or 0)
                        ratio = float(row.get('rs_ratio', 100))
                        
                        alerts = []
                        # Logic Trụ Kéo
                        if ratio > 100 and conc > 0.6 and breadth < 0.3:
                            alerts.append("⚠️ TRỤ KÉO")
                        # Logic Lan Tỏa
                        if ratio > 100 and breadth > 0.7:
                            alerts.append("🌊 LAN TỎA")
                        # Logic Tiền Vào
                        if shock > 1.5:
                            alerts.append("🔥 TIỀN VÀO")
                            
                        return ", ".join(alerts) if alerts else "Bình thường"

                    xray_df['Chẩn đoán'] = xray_df.apply(diagnose, axis=1)
                    
                    # 4. Display Table (Chọn cột hiển thị)
                    display_cols = ['sector', 'rs_ratio', 'rs_momentum', 'breadth', 'concentration', 'turnover_shock_z', 'Chẩn đoán']
                    
                    # Format Style
                    def color_row(row):
                        val = row['Chẩn đoán']
                        if "TRỤ KÉO" in val: return ['background-color: #ffebee'] * len(row)
                        if "LAN TỎA" in val: return ['background-color: #e8f5e9'] * len(row)
                        if "TIỀN VÀO" in val: return ['background-color: #fffde7'] * len(row)
                        return [''] * len(row)

                    st.dataframe(
                        xray_df[display_cols].style.apply(color_row, axis=1)
                                             .format({
                                                 'rs_ratio': '{:.1f}',
                                                 'rs_momentum': '{:.1f}',
                                                 'breadth': '{:.0%}',
                                                 'concentration': '{:.0%}',
                                                 'turnover_shock_z': '{:.1f}σ'
                                             }),
                        use_container_width=True,
                        height=500
                    )
                else:
                    st.error("❌ Không có dữ liệu Sector Stats (df_latest is empty). Vui lòng kiểm tra Backend.")

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
