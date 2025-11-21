"""
Watchlist Management Dashboard - Stock Hunter AI
=================================================
P0 Critical Feature: Dynamic Watchlist Management

Allows Admin to:
- View current watchlist (up to 500 symbols per NFR10)
- Add new symbols with sector classification
- Remove/deactivate symbols
- Monitor NFR10 compliance (≤ 500 symbols)

PRD Story: Dynamic Watchlist Management (GAP #7)
"""

import streamlit as st
import httpx
import os
import pandas as pd

# --- Configuration ---
API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
ADMIN_KEY = os.getenv("ADMIN_KEY", "supersecretkey")
HEADERS = {"X-ADMIN-KEY": ADMIN_KEY}

# --- Helper Functions ---

@st.cache_data(ttl=30)  # Cache for 30 seconds
def fetch_watchlist(include_inactive: bool = False):
    """Fetch current watchlist from API."""
    try:
        url = f"{API_BASE_URL}/admin/watchlist"
        params = {"include_inactive": include_inactive}
        response = httpx.get(url, headers=HEADERS, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        st.error(f"HTTP error fetching watchlist: {e.response.status_code} - {e.response.text}")
        return None
    except httpx.RequestError as e:
        st.error(f"Connection error fetching watchlist: {e}")
        return None


@st.cache_data(ttl=60)  # Cache for 1 minute
def fetch_watchlist_stats():
    """Fetch watchlist statistics."""
    try:
        url = f"{API_BASE_URL}/admin/watchlist/stats"
        response = httpx.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        st.error(f"HTTP error fetching stats: {e.response.status_code} - {e.response.text}")
        return None
    except httpx.RequestError as e:
        st.error(f"Connection error fetching stats: {e}")
        return None



@st.cache_data(ttl=3600)  # Cache for 1 hour (taxonomy rarely changes)
def fetch_sectors():
    """Fetch sector taxonomy from API."""
    try:
        url = f"{API_BASE_URL}/admin/sectors"
        response = httpx.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        st.error(f"Error fetching sectors: {e}")
        return []


def add_symbol(symbol: str, sector: str, market_cap_tier: str = None):
    """Add symbol to watchlist."""
    try:
        url = f"{API_BASE_URL}/admin/watchlist"
        payload = {
            "symbol": symbol.strip().upper(),
            "sector": sector,
            "market_cap_tier": market_cap_tier if market_cap_tier else None
        }
        response = httpx.post(url, headers=HEADERS, json=payload, timeout=10)
        response.raise_for_status()
        return True, response.json()
    except httpx.HTTPStatusError as e:
        return False, e.response.json().get('detail', str(e))
    except httpx.RequestError as e:
        return False, str(e)


def remove_symbol(symbol: str, permanent: bool = False):
    """Remove symbol from watchlist."""
    try:
        url = f"{API_BASE_URL}/admin/watchlist/{symbol.strip().upper()}"
        params = {"permanent": permanent}
        response = httpx.delete(url, headers=HEADERS, params=params, timeout=10)
        response.raise_for_status()
        return True, response.json()
    except httpx.HTTPStatusError as e:
        return False, e.response.json().get('detail', str(e))
    except httpx.RequestError as e:
        return False, str(e)


# --- UI Layout ---
st.set_page_config(layout="wide", page_title="Watchlist Management")
st.title("📋 Watchlist Management")
st.caption("Manage symbols for Stock Hunter AI scraper (Max 500 symbols - NFR10)")

# --- Statistics Section ---
st.header("📊 Watchlist Statistics")

stats = fetch_watchlist_stats()

if stats:
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric(
            "Active Symbols",
            f"{stats['active_symbols']}/{stats['max_symbols']}",
            delta=None,
            help="Number of active symbols being tracked"
        )

    with col2:
        capacity = stats['capacity_used_pct']
        delta_color = "inverse" if capacity < 90 else "off"
        st.metric(
            "Capacity Used",
            f"{capacity:.1f}%",
            delta=None,
            delta_color=delta_color,
            help="Percentage of max capacity used (NFR10: ≤ 500 symbols)"
        )

    with col3:
        st.metric(
            "Inactive Symbols",
            stats['inactive_symbols'],
            delta=None,
            help="Deactivated symbols (can be reactivated)"
        )

    with col4:
        compliance = stats['compliance']
        compliant_text = "✅ COMPLIANT" if compliance['nfr10_compliant'] else "❌ NON-COMPLIANT"
        st.metric(
            "NFR10 Status",
            compliant_text,
            delta=None,
            help="Compliance with NFR10: ≤ 500 symbols requirement"
        )

    # Warning banner
    if stats['compliance']['warning']:
        if stats['capacity_used_pct'] >= 100:
            st.error(f"⚠️ {stats['compliance']['warning']}")
        else:
            st.warning(f"⚠️ {stats['compliance']['warning']}")

    # Sector breakdown
    st.subheader("By Sector")
    if stats['by_sector']:
        sector_df = pd.DataFrame([
            {"Sector": sector.replace('_', ' ').title(), "Count": count}
            for sector, count in stats['by_sector'].items()
        ])
        st.dataframe(sector_df, use_container_width=True, hide_index=True)
    else:
        st.info("No symbols in watchlist")

else:
    st.warning("Could not load statistics. Check API connection.")

st.divider()

# --- Add Symbol Section ---
st.header("➕ Add Symbol")

with st.form("add_symbol_form"):
    col1, col2, col3 = st.columns([2, 2, 2])

    with col1:
        new_symbol = st.text_input(
            "Symbol",
            placeholder="e.g., HPG",
            help="Stock symbol (will be converted to uppercase)",
            max_chars=10
        )

    with col2:
        # Fetch sectors dynamically
        sectors_data = fetch_sectors()
        
        # Group by Super Sector
        sector_options = {}
        if sectors_data:
            for item in sectors_data:
                super_sec = item['super_sector'] or "Other"
                if super_sec not in sector_options:
                    sector_options[super_sec] = []
                sector_options[super_sec].append((item['sector'], item['display_name']))
        
        # Create selectbox options with formatting
        flat_options = []
        option_map = {} # label -> sector_code
        
        # Custom order for 4 Pillars
        pillar_order = ["Financials", "Real_Estate_Chain", "Production_Export", "Consumer_Tech", "Utilities"]
        
        sorted_super_sectors = sorted(sector_options.keys(), key=lambda x: pillar_order.index(x) if x in pillar_order else 99)
        
        for super_sec in sorted_super_sectors:
            for sec_code, sec_name in sector_options[super_sec]:
                label = f"{super_sec}: {sec_name} ({sec_code})"
                flat_options.append(label)
                option_map[label] = sec_code
                
        selected_label = st.selectbox(
            "Sector",
            options=flat_options,
            help="Select sector (grouped by 4 Pillars)"
        )
        
        sector = option_map.get(selected_label) if selected_label else None

    with col3:
        market_cap_tier = st.selectbox(
            "Market Cap (Optional)",
            ["", "large", "mid", "small"],
            help="Market capitalization tier (optional)"
        )

    submitted = st.form_submit_button("Add to Watchlist", type="primary")

    if submitted:
        if not new_symbol:
            st.error("Please enter a symbol")
        elif len(new_symbol) < 2:
            st.error("Symbol must be at least 2 characters")
        else:
            # Clear cache before adding
            fetch_watchlist.clear()
            fetch_watchlist_stats.clear()

            success, result = add_symbol(
                new_symbol,
                sector,
                market_cap_tier if market_cap_tier else None
            )

            if success:
                st.success(f"✅ {result['message']}")
                st.balloons()
                st.rerun()  # Refresh page to show new symbol
            else:
                st.error(f"❌ Failed to add symbol: {result}")

st.divider()

# --- Current Watchlist Section ---
st.header("📈 Current Watchlist")

# Include inactive toggle
include_inactive = st.checkbox("Show inactive symbols", value=False)

watchlist_data = fetch_watchlist(include_inactive=include_inactive)

if watchlist_data and watchlist_data['symbols']:
    symbols = watchlist_data['symbols']

    # Convert to DataFrame
    df = pd.DataFrame(symbols)

    # Format columns
    if not df.empty:
        # Reorder columns
        column_order = ['symbol', 'sector', 'market_cap_tier', 'is_active', 'added_at']
        df = df[[col for col in column_order if col in df.columns]]

        # Rename for display
        df = df.rename(columns={
            'symbol': 'Symbol',
            'sector': 'Sector',
            'market_cap_tier': 'Market Cap',
            'is_active': 'Active',
            'added_at': 'Added'
        })

        # Format sector
        df['Sector'] = df['Sector'].str.replace('_', ' ').str.title()

        # Display count
        st.caption(f"Showing {len(df)} symbols")

        # Data editor (read-only for now, delete handled separately)
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Symbol": st.column_config.TextColumn("Symbol", width="small"),
                "Sector": st.column_config.TextColumn("Sector", width="medium"),
                "Market Cap": st.column_config.TextColumn("Market Cap", width="small"),
                "Active": st.column_config.CheckboxColumn("Active", width="small"),
                "Added": st.column_config.DatetimeColumn("Added", format="YYYY-MM-DD HH:mm", width="medium")
            }
        )

        # Remove symbol section
        st.subheader("➖ Remove Symbol")

        col1, col2, col3 = st.columns([3, 2, 2])

        with col1:
            symbol_to_remove = st.selectbox(
                "Select symbol to remove",
                options=df['Symbol'].tolist(),
                help="Choose a symbol to deactivate or delete"
            )

        with col2:
            permanent_delete = st.checkbox(
                "Permanent delete",
                value=False,
                help="If checked, symbol will be permanently deleted. Otherwise, just deactivated."
            )

        with col3:
            st.write("")  # Spacing
            st.write("")  # Spacing
            if st.button("🗑️ Remove", type="secondary"):
                # Clear cache before removing
                fetch_watchlist.clear()
                fetch_watchlist_stats.clear()

                success, result = remove_symbol(symbol_to_remove, permanent=permanent_delete)

                if success:
                    action = "permanently deleted" if permanent_delete else "deactivated"
                    st.success(f"✅ Symbol {symbol_to_remove} {action}")
                    st.rerun()  # Refresh page
                else:
                    st.error(f"❌ Failed to remove symbol: {result}")

    else:
        st.info("No symbols in watchlist. Add some symbols above to get started.")

else:
    st.info("Watchlist is empty or could not be loaded.")

st.divider()

# --- Help Section ---
with st.expander("ℹ️ Help & Guidelines"):
    st.markdown("""
    ### Adding Symbols
    - Enter stock symbol (e.g., FPT, VNM, HPG)
    - Select appropriate sector
    - Optionally specify market cap tier
    - Maximum **500 active symbols** (NFR10 requirement)

    ### Removing Symbols
    - **Deactivate** (default): Symbol remains in database but won't be scraped
    - **Permanent delete**: Symbol completely removed from database

    ### Sector Classifications (4 Pillars + 1)
    The system now uses the "4 Pillars + 1" taxonomy tailored for Vietnam:
    1. **Financials & Capital**: Banking, Securities, Insurance
    2. **Real Estate & Value Chain**: Real Estate, Industrial RE, Construction, Materials
    3. **Production & Export**: Energy, Agriculture, Manufacturing, Chemicals, Logistics
    4. **Consumer & Tech**: Retail, Technology, F&B
    5. **Utilities**: Power, Water, Pharma


    ### NFR10 Compliance
    The system enforces a **maximum of 500 active symbols** to ensure:
    - Optimal scraper performance
    - Manageable data volume
    - Compliance with rate limits

    When approaching the 500 symbol limit, consider:
    1. Deactivating underperforming stocks
    2. Focusing on high-conviction positions
    3. Reviewing sector diversification
    """)

# --- Refresh Button ---
if st.button("🔄 Refresh Data"):
    fetch_watchlist.clear()
    fetch_watchlist_stats.clear()
    st.rerun()

st.caption("💡 Tip: Use the scraper logs to verify symbols are being fetched correctly")
