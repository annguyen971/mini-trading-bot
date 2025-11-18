import streamlit as st

# The authentication check is now implicitly handled by the utils.py file
# when it's imported by each page. It will check for the ADMIN_KEY.

st.set_page_config(
    page_title="Stock Hunter AI",
    page_icon="🎯",
    layout="wide",
)

st.title("Stock Hunter AI - Control Plane")
st.info("Select a page from the sidebar to get started.")

st.markdown("""
### Onboarding Checklist:
- [ ] **Set `ADMIN_KEY`**: Ensure the `ADMIN_KEY` environment variable is set in your `.env` file or Streamlit secrets.
- [ ] **Check API URL**: Verify that the `API_BASE_URL` is correctly pointing to your running API service (e.g., `http://api:8000` in Docker).
- [ ] **Explore Overview**: Enter a symbol to see the main dashboard.
- [ ] **Try Active Learning**: Visit the Active Learning page to see pending review tasks.
""")
