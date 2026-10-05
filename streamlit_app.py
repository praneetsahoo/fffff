"""OpsIntel — Streamlit entry point.

Run locally:   streamlit run streamlit_app.py
"""
from __future__ import annotations

import streamlit as st

from opsintel import config, db, storage
from views import dashboard, quality, records, upload

st.set_page_config(page_title="OpsIntel", page_icon="📊", layout="wide")
config.setup_logging()


@st.cache_resource(show_spinner=False)
def start_up() -> bool:
    """Runs once per server start: create tables if they don't exist yet."""
    db.create_schema()
    return True


@st.cache_data(ttl=30, show_spinner=False)
def health() -> tuple[bool, bool]:
    return db.is_available(), storage.is_available()


db_ok, storage_ok = health()
if db_ok:
    start_up()

with st.sidebar:
    st.markdown("### OpsIntel")
    st.caption("Upload operational data → validated, cleaned and stored on AWS → live dashboard.")
    st.markdown(f"{'🟢' if db_ok else '🔴'} Database (RDS MySQL) {'connected' if db_ok else 'unavailable'}")
    store_name = "Amazon S3" if storage.using_s3() else "Local storage"
    st.markdown(f"{'🟢' if storage_ok else '🔴'} {store_name} {'connected' if storage_ok else 'unavailable'}")
    st.divider()
    st.caption("Python · pandas · SQL · Streamlit · AWS (S3, RDS, EC2, IAM, CloudWatch)")

if not db_ok:
    st.error("The database is not reachable right now, so nothing can be shown or uploaded. "
             "The team has been alerted through CloudWatch. Please try again in a minute.")
    st.stop()

page = st.navigation([
    st.Page(upload.render, title="Upload", icon="⬆️", url_path="upload", default=True),
    st.Page(dashboard.render, title="Dashboard", icon="📈", url_path="dashboard"),
    st.Page(records.render, title="Records", icon="🔎", url_path="records"),
    st.Page(quality.render, title="Data quality", icon="✅", url_path="quality"),
], position="top")
page.run()
