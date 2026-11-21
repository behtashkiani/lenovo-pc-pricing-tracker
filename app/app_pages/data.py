"""Data and methods: where the prices come from and what the tracker could not use.
Nothing on this page depends on a comparison group."""
from __future__ import annotations

import pandas as pd
import streamlit as st

import charts
from common import anchor_brand, data_footer, get_data, is_dark
from tracker import dashboard as dd

data = get_data()
dark = is_dark()
anchor = anchor_brand()

with st.sidebar:
    st.header("Data and methods")
    st.caption("How the prices were collected, which Lenovo configurations have no match, which listings "
               "could not be read, and the log of every run.")
    st.divider()
    data_footer(data)

if data.prices.empty:
    st.info("Nothing collected yet. Run `python collect_top_laptops.py --all-brands`.")
    st.stop()

tab_methods, tab_unmatched, tab_review, tab_runs = st.tabs(
    ["Collection methods", f"Unmatched {anchor} specs", "Review queue", "Runs"])

# --------------------------------------------------------------------------- collection methods
with tab_methods:
    st.markdown("**Coverage by collection method**")
    coverage = dd.source_coverage(data.prices)
    coverage["source"] = coverage["source"].map(dd.SOURCE_LABEL).fillna(coverage["source"])
    st.dataframe(coverage, hide_index=True, width="stretch",
                 alt="Price rows, laptops and date range per collection method",
                 column_config={
                     "method": "Method", "source": "Kind of row",
                     "rows": st.column_config.NumberColumn("Price rows", format="localized"),
                     "laptops": st.column_config.NumberColumn("Laptops", format="localized"),
                     "first": st.column_config.DateColumn("First day", format="D MMM YYYY"),
                     "last": st.column_config.DateColumn("Last day", format="D MMM YYYY")})

    st.markdown("**Compare methods for one laptop**")
    priced = sorted(set(data.prices["sku"]) & set(data.products.index))
    brands = dd.brand_of(priced, data)
    names = dd.short_names(priced, data)
    ordered = sorted(priced, key=lambda s: (brands[s].lower() != anchor.lower(), brands[s], names[s]))
    pick = st.selectbox("Laptop", ordered, format_func=lambda s: f"{brands[s]} {names[s]}  (SKU {s})",
                        label_visibility="collapsed", key="d-laptop")
    one = data.prices[(data.prices["sku"] == pick) & ~data.prices["flagged"]].copy()
    one["method"] = one["source"].map(dd.METHOD)
    lines = []
    method_colors = charts.source_colors(dark)
    if not one.empty:
        days = pd.date_range(one["date"].min(), one["date"].max(), freq="D")
        for method, part in one.dropna(subset=["method"]).groupby("method", sort=False):
            part = part.sort_values("date").drop_duplicates("date")
            y = part.set_index("date")["price"].reindex(days)
            observed = part.set_index("date")["source"].isin([dd.OWN, "honey", "shopsavvy"]).reindex(days)
            lines.append({"name": method, "color": method_colors.get(method, charts.NEUTRAL), "y": y,
                          "markers": observed if int(observed.eq(True).sum()) <= 40 else observed & False})
        lines.sort(key=lambda line: list(method_colors).index(line["name"]) if line["name"] in method_colors else 9)
        st.plotly_chart(charts.sources_figure(lines, dark=dark), width="stretch", theme="streamlit",
                        config={"displayModeBar": False}, alt="Price of one laptop over time, one line per method")
        st.caption("One line per collection method, never merged. Dots are days the method actually recorded a "
                   "price; between them its line carries the last price forward.")
    else:
        st.info("No prices for this laptop.")

# --------------------------------------------------------------------------- unmatched anchor specs
with tab_unmatched:
    groups = data.groups
    alone = groups[(groups["level"] == "exact") & groups["has_anchor"] & (groups["competitor_count"] == 0)] \
        if not groups.empty else groups
    st.caption(f"{anchor} configurations with no identical configuration from another brand at Best Buy "
               f"({len(alone)} exact groups). They are not priced daily.")
    if not alone.empty:
        latest_own = (data.prices[(data.prices["source"] == dd.OWN) & ~data.prices["flagged"]]
                      .sort_values("date").groupby("sku")["price"].last())
        table = pd.DataFrame({
            "spec": alone["label"],
            "laptops": alone["member_count"],
            "from": [min((latest_own[s] for s in g if s in latest_own.index), default=None) for g in alone["skus"]],
        }).sort_values("spec")
        st.dataframe(table, hide_index=True, width="stretch",
                     alt=f"{anchor} specifications without a competitor, with laptop count and lowest price",
                     column_config={
                         "spec": "Specification", "laptops": st.column_config.NumberColumn("Laptops"),
                         "from": st.column_config.NumberColumn("From", format="dollar",
                                                               help="Lowest price when last collected.")})

# --------------------------------------------------------------------------- review queue
with tab_review:
    specs = data.specs
    queue = specs[specs["needs_review"].eq(True)] if "needs_review" in specs else specs.iloc[0:0]
    st.caption(f"{len(queue)} of {len(specs)} laptops could not be normalised and are in no group. "
               "A key field that cannot be read is left empty rather than guessed.")
    if not queue.empty:
        st.dataframe(pd.DataFrame({
            "sku": queue.index,
            "brand": [dd.value(specs, s, "brand") for s in queue.index],
            "title": [dd.value(data.products, s, "title") for s in queue.index],
            "why": ["; ".join(dd.value(specs, s, "review_notes") or []) for s in queue.index],
        }), hide_index=True, width="stretch", alt="Listings held for review and the reason",
            column_config={"sku": "SKU", "brand": "Brand", "title": "Listing", "why": "What could not be read"})

# --------------------------------------------------------------------------- runs
with tab_runs:
    if data.runs.empty:
        st.info("No runs logged yet.")
    else:
        log = data.runs.sort_values("id", ascending=False).head(30).copy()
        for col in ("started_at", "finished_at"):
            log[col] = pd.to_datetime(log[col], errors="coerce", utc=True).dt.tz_localize(None)
        cols = [c for c in ("started_at", "kind", "source", "status", "products_ok", "products_failed", "error")
                if c in log]
        st.dataframe(log[cols], hide_index=True, width="stretch", alt="Log of collection runs, newest first",
                     column_config={
                         "started_at": st.column_config.DatetimeColumn("Started (UTC)", format="D MMM YYYY, HH:mm"),
                         "kind": "Job", "source": "Source", "status": "Status",
                         "products_ok": st.column_config.NumberColumn("OK"),
                         "products_failed": st.column_config.NumberColumn("Failed"), "error": "Error"})
