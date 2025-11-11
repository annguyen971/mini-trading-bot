# src/dash/app.py
import streamlit as st
import os
from datetime import datetime, timedelta

def check_auth():
    """
    Checks if a user is authenticated. If not, it displays a login form.
    Returns True if authenticated, otherwise stops the script execution.
    This serves as the backup authentication layer as per the spec.
    """
    auth_time = st.session_state.get("auth_time")
    if st.session_state.get("auth_ok") and auth_time and (datetime.now() - auth_time) < timedelta(hours=8):
        return True

    st.title("Stock Hunter AI - Admin Access")
    st.write("Please provide the admin key to access the dashboard.")

    admin_key_from_env = os.getenv("ADMIN_KEY")
    if not admin_key_from_env:
        st.error("FATAL: ADMIN_KEY is not set in the environment. The dashboard cannot start.")
        st.stop()

    password = st.text_input("Admin Key", type="password", key="password_input")

    if st.button("Login"):
        if password == admin_key_from_env:
            st.session_state["auth_ok"] = True
            st.session_state["auth_time"] = datetime.now()
            st.rerun()
        else:
            st.error("The provided key is incorrect.")
            st.session_state["auth_ok"] = False

    st.stop()

# --- Main Application ---
st.set_page_config(layout="wide", page_title="Stock Hunter AI")

check_auth()

st.title("Welcome to Stock Hunter AI")
st.success("Authentication successful.")
st.write("Please select a page from the sidebar to begin.")
st.page_link("pages/01_Overview.py", label="Go to Main Dashboard")
