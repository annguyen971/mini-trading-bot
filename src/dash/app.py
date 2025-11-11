# src/dash/app.py
import streamlit as st
import os
from datetime import datetime, timedelta

def check_auth():
    """
    Checks if a user is authenticated. If not, it displays a login form.
    Returns True if authenticated, otherwise stops the script execution.
    This serves as the backup authentication layer as per the spec.
    The primary authentication (e.g., via X-ADMIN-KEY) is expected
    to be handled by the proxy server.
    """
    # If the user is already authenticated and the session is not expired,
    # grant access immediately.
    auth_time = st.session_state.get("auth_time")
    if st.session_state.get("auth_ok") and auth_time and (datetime.now() - auth_time) < timedelta(hours=8):
        return True

    # If not authenticated, display the login form.
    st.title("Stock Hunter AI - Admin Access")
    st.write("Please provide the admin key to access the dashboard.")

    # The application is not usable without the key being set in the environment.
    admin_key_from_env = os.getenv("ADMIN_KEY")
    if not admin_key_from_env:
        st.error("FATAL: ADMIN_KEY is not set in the environment. The dashboard cannot start.")
        st.stop()

    # Password input form
    password = st.text_input("Admin Key", type="password", key="password_input")

    if st.button("Login"):
        if password == admin_key_from_env:
            # On successful login, set session state and rerun the script
            # to hide the login form and show the page content.
            st.session_state["auth_ok"] = True
            st.session_state["auth_time"] = datetime.now()
            st.rerun()
        else:
            st.error("The provided key is incorrect.")
            st.session_state["auth_ok"] = False

    # Stop execution to prevent the rest of the page from loading
    # for unauthenticated users.
    st.stop()

# --- Main Application ---
st.set_page_config(layout="wide", page_title="Stock Hunter AI")

# The main entry page simply calls the auth gate and then shows a welcome message.
# Navigation to other pages is done via the sidebar which Streamlit creates
# from the files in the `pages/` directory.
check_auth()

st.title("Welcome to Stock Hunter AI")
st.success("Authentication successful.")
st.write("Please select a page from the sidebar to begin.")
st.page_link("pages/01_Overview.py", label="Go to Main Dashboard")
