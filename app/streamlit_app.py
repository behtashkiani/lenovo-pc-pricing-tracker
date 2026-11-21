"""PC Pricing Tracker dashboard.

    streamlit run app/streamlit_app.py

Three pages: the comparison groups (Lenovo against the brands with matching specs), a
spec explorer (pick any combination of specs and see what those laptops cost over time),
and data and methods (collection methods, unmatched specs, review queue, run log).
Reads Supabase when SUPABASE_DB_URL is configured and reachable, otherwise the local store.
"""
from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="PC Pricing Tracker", page_icon=":material/laptop_chromebook:", layout="wide")

page = st.navigation(
    [
        st.Page("app_pages/groups.py", title="Comparison groups", icon=":material/compare_arrows:", default=True,
                help="Laptops with equivalent specs, Lenovo against the other brands: headline figures, "
                     "price trend and a table for the group you pick."),
        st.Page("app_pages/explorer.py", title="Spec explorer", icon=":material/tune:",
                help="Filter every collected laptop by processor, memory, storage, form factor and more, "
                     "and chart the prices of whatever matches."),
        st.Page("app_pages/data.py", title="Data and methods", icon=":material/fact_check:",
                help="Where the prices come from: coverage per collection method, Lenovo specs with no match, "
                     "listings held for review, and the run log."),
    ],
    position="top",
)
page.run()
