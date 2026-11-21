"""Spec explorer: choose any combination of specs and chart the prices of the laptops that match."""
from __future__ import annotations

import streamlit as st

from common import data_footer, get_data, render_trend
from tracker import dashboard as dd

ANY, TOUCH, NO_TOUCH = "Any", "Touch", "Non-touch"
LABELS = {"ssd": "SSD", "ufs": "UFS", "emmc": "eMMC", "hdd": "HDD", "hybrid": "SSD+HDD",
          "windows": "Windows", "chromeos": "ChromeOS", "googlebook": "Googlebook OS", "macos": "macOS",
          "linux": "Linux", "android": "Android", "none": "No OS",
          "clamshell": "Clamshell", "convertible": "Convertible (2-in-1)", "detachable": "Detachable",
          "dual-screen": "Dual-screen"}

data = get_data()
table = dd.spec_table(data)
if table.empty:
    st.info("Nothing to explore yet. Run `python collect_top_laptops.py --all-brands` and `python build_groups.py`.")
    st.stop()


def options(column: str) -> list:
    return sorted(table[column].dropna().unique().tolist())


def label(value: str) -> str:
    return LABELS.get(value, str(value))


def reset() -> None:
    for name in list(st.session_state):
        if name.startswith("x-"):
            del st.session_state[name]


memory_steps = [int(v) for v in options("memory_gb")]
storage_steps = [int(v) for v in options("storage_gb")]
sizes = table["screen_size_in"].dropna().astype(float)
prices = table["price"].dropna()
price_low, price_high = int(prices.min() // 50 * 50), int(-(-prices.max() // 50) * 50)

# --------------------------------------------------------------------------- filters
with st.sidebar:
    st.header("Spec explorer")
    st.caption("Leave a list empty to accept everything. Every filter narrows the others.")
    brands = st.multiselect("Brand", options("brand"), key="x-brand", placeholder="All brands")
    families = st.multiselect("Processor family", options("cpu_family"), key="x-family", placeholder="All families",
                              help="Family, tier and generation, for example Core Ultra 7 Series 2.")
    cpus = st.multiselect("Processor model", options("cpu_name"), key="x-cpu", placeholder="All models")
    memory = st.select_slider("Memory", memory_steps, value=(memory_steps[0], memory_steps[-1]), key="x-memory",
                              format_func=lambda v: f"{v} GB")
    storage = st.select_slider("Storage", storage_steps, value=(storage_steps[0], storage_steps[-1]),
                               key="x-storage", format_func=dd.capacity_label)
    storage_types = st.pills("Storage type", options("storage_type"), selection_mode="multi", key="x-storage-type",
                             format_func=label)
    form_factors = st.pills("Form factor", options("form_factor"), selection_mode="multi", key="x-form",
                            format_func=label)
    touch = st.segmented_control("Touchscreen", [ANY, TOUCH, NO_TOUCH], default=ANY, required=True, key="x-touch")
    systems = st.pills("Operating system", options("os"), selection_mode="multi", key="x-os", format_func=label)
    graphics = st.multiselect("Graphics", ["Integrated"] + [g for g in options("graphics") if g != "Integrated"],
                              key="x-graphics", placeholder="Any graphics")
    screen = st.slider("Screen size (inches)", float(sizes.min()), float(sizes.max()),
                       (float(sizes.min()), float(sizes.max())), step=0.1, format="%.1f", key="x-screen")
    price = st.slider("Price now", price_low, price_high, (price_low, price_high), step=50, format="$%d",
                      key="x-price")
    st.button("Reset filters", icon=":material/restart_alt:", on_click=reset, width="stretch")
    st.divider()
    data_footer(data)

# A range left at its full width is no constraint: laptops with no screen size or price on file stay in.
matches = dd.filter_specs(
    table, brands=brands, cpu_families=families, cpus=cpus,
    memory=memory if tuple(memory) != (memory_steps[0], memory_steps[-1]) else None,
    storage=storage if tuple(storage) != (storage_steps[0], storage_steps[-1]) else None,
    storage_types=storage_types, form_factors=form_factors,
    touch=None if touch == ANY else touch == TOUCH, os=systems, graphics=graphics,
    screen=screen if tuple(screen) != (float(sizes.min()), float(sizes.max())) else None,
    price=price if tuple(price) != (price_low, price_high) else None)

# --------------------------------------------------------------------------- result
chosen = [text for text in (
    ", ".join(brands), ", ".join(families), ", ".join(cpus),
    f"{memory[0]}–{memory[1]} GB memory" if tuple(memory) != (memory_steps[0], memory_steps[-1]) else "",
    f"{dd.capacity_label(storage[0])}–{dd.capacity_label(storage[1])} storage"
    if tuple(storage) != (storage_steps[0], storage_steps[-1]) else "",
    ", ".join(label(v) for v in storage_types), ", ".join(label(v) for v in form_factors),
    touch.lower() if touch != ANY else "", ", ".join(label(v) for v in systems), ", ".join(graphics),
    f'{screen[0]:g}–{screen[1]:g}"' if tuple(screen) != (float(sizes.min()), float(sizes.max())) else "",
    f"${price[0]:,}–${price[1]:,}" if tuple(price) != (price_low, price_high) else "") if text]

st.subheader(" · ".join(chosen) if chosen else "All laptops")
st.caption(f"{len(matches)} of {len(table)} laptops match. New laptops sold by Best Buy itself; the "
           f"{len(data.specs) - len(table)} listings that could not be fully normalised are left out.")

if matches.empty:
    st.info("No laptop has this combination of specs. Widen a range or clear a filter.", icon=":material/filter_alt:")
else:
    render_trend(data, matches.index.tolist(), key="x-trend", count_label="Matching", brand_filter=False)
