# src/dash/utils.py
import os
import streamlit as st
import httpx

# --- Centralized API Configuration ---

# Try to get the API_BASE_URL from secrets, otherwise fall back to environment variable
try:
    API_BASE_URL = st.secrets.get("API_BASE_URL", os.getenv("API_BASE_URL", "http://api:8000"))
    ADMIN_KEY = st.secrets.get("ADMIN_KEY", os.getenv("ADMIN_KEY"))
except Exception:
    API_BASE_URL = os.getenv("API_BASE_URL", "http://api:8000")
    ADMIN_KEY = os.getenv("ADMIN_KEY")


# Check if the admin key is configured
if not ADMIN_KEY:
    st.error("FATAL: `ADMIN_KEY` is not configured in environment variables or Streamlit secrets.")
    st.stop()

# Standard headers for all API calls
HEADERS = {"X-ADMIN-KEY": ADMIN_KEY}

def get_api_client() -> httpx.Client:
    """
    Returns a pre-configured httpx client with the base URL and authentication headers.
    """
    return httpx.Client(base_url=API_BASE_URL, headers=HEADERS, timeout=15.0)

# --- General Purpose Functions ---

def show_safety_banner(banner_data: dict):
    """Displays a status banner based on a dictionary of data."""
    if not isinstance(banner_data, dict):
        return

    level = banner_data.get("level", "info")
    title = banner_data.get("title", "Status")
    message = banner_data.get("message", "System is operating normally.")

    if level == "error":
        st.error(f"**{title}**: {message}")
    elif level == "warning":
        st.warning(f"**{title}**: {message}")
    else:
        st.info(f"**{title}**: {message}")
