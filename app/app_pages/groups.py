"""Comparison groups: laptops whose normalised specs match, Lenovo against the other brands."""
from __future__ import annotations

import streamlit as st

from common import anchor_brand, data_footer, get_data, render_trend
from tracker.normalise import LEVELS, SCREEN_BUCKETS

LEVEL_TEXT = {
    "strict": "Strict match: an exact match (same CPU model, memory, storage size and type, form factor, "
              f"touchscreen, OS and graphics) plus the same screen size bucket ({', '.join(SCREEN_BUCKETS)} inches).",
    "exact": "Exact match: same CPU model, memory, storage size and type, form factor, touchscreen, OS and "
             "graphics. Screen size may differ.",
    "relaxed": "Relaxed match: same CPU model, memory, storage size, form factor, OS and graphics. "
               "Touchscreen, storage type and screen size may differ.",
    "class": "Class match: same CPU family, tier and generation, memory, storage size, form factor, OS and "
             "graphics. The exact CPU model may differ, so each laptop's own CPU is listed below; so may "
             "touchscreen, storage type and screen size.",
}

data = get_data()
anchor = anchor_brand()
groups = data.groups

if groups.empty or data.prices.empty:
    st.title("PC Pricing Tracker")
    st.info("Nothing to chart yet. Run `python collect_top_laptops.py --all-brands`, then "
            "`python build_groups.py` and `python get_prices_now.py`.")
    st.stop()


def competitors(group) -> list[str]:
    return [b for b in group["brands"] if b.lower() != anchor.lower()]


# --------------------------------------------------------------------------- sidebar: which group
with st.sidebar:
    st.header("PC Pricing Tracker")
    st.caption(f"Best Buy laptop prices, grouped by equivalent specification. Anchor brand: {anchor}.")
    scope = st.segmented_control("Groups", ["Featured", "All tracked"], default="Featured", required=True)
    if scope == "Featured":
        pool = groups[groups["featured_rank"].notna()].sort_values("featured_rank")
    else:
        level = st.segmented_control("Match level", list(LEVELS), default="exact", required=True,
                                     format_func=str.capitalize,
                                     help="Strictest first. Each level ignores more than the one before it; "
                                          "the table under the chart lists what the chosen level ignores.")
        pool = groups[(groups["level"] == level) & groups["tracked"]].sort_values(
            ["competitor_count", "member_count", "label"], ascending=[False, False, True])
    if pool.empty:
        st.info("No group matches. Run `python build_groups.py`.")
        st.stop()
    by_key = {g["group_key"]: g for _, g in pool.iterrows()}
    keys = list(by_key)
    if scope == "Featured":
        chosen = st.radio(
            "Comparison group", keys,
            format_func=lambda k: by_key[k]["label"],
            captions=[f"{by_key[k]['level']} match · vs {', '.join(competitors(by_key[k]))}" for k in keys])
    else:
        chosen = st.selectbox(
            "Comparison group", keys,
            format_func=lambda k: f"{by_key[k]['label']}  ({by_key[k]['competitor_count']} other brands)")
    group = by_key[chosen]

    st.divider()
    data_footer(data)

skus = [s for s in group["skus"] if s in data.products.index]

# --------------------------------------------------------------------------- header
st.subheader(group["label"])
st.caption(f"{anchor} against {', '.join(competitors(group)) or 'no other brand'} · {len(skus)} laptops · "
           + LEVEL_TEXT[group["level"]])

render_trend(data, skus, key=f"g-{chosen}", level=group["level"])
