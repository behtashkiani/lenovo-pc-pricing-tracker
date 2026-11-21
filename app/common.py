"""Shared by the dashboard pages: data loading, theme, and the price-trend section
(filters, headline figures, chart, table) that every page draws for a set of laptops."""
from __future__ import annotations

import html
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import charts  # noqa: E402  (sibling module)
from tracker import config  # noqa: E402
from tracker import dashboard as dd  # noqa: E402
from tracker.normalise import canonical_brand  # noqa: E402

OWN_ONLY, MERGED = "Own scraper only", "Own scraper + backfill"
BY_BRAND, BY_LAPTOP = "Lowest price per brand", "Every laptop"
PERIODS = {"30d": 30, "90d": 90, "180d": 180, "All": None}
MINUS = "−"
MAX_LAPTOP_LINES = 40


# --------------------------------------------------------------------------- data
def _stamp() -> float:
    files = ("products.jsonl", "product_specs.jsonl", "spec_groups.json", "price_snapshots.jsonl", "scrape_runs.jsonl")
    return sum((config.DATA_DIR / f).stat().st_mtime for f in files if (config.DATA_DIR / f).exists())


@st.cache_data(ttl=600, show_spinner="Loading prices…")
def load_data(stamp: float) -> dd.Data:
    return dd.load()


def get_data() -> dd.Data:
    return load_data(_stamp())


def anchor_brand() -> str:
    return canonical_brand(config.ANCHOR_BRAND) or "Lenovo"


def is_dark() -> bool:
    try:
        return st.context.theme.type == "dark"
    except Exception:  # noqa: BLE001 - theme is not always known on the first run
        return False


def esc(text) -> str:
    return html.escape(str(text), quote=False)     # Plotly hover text does not decode &quot;


def data_footer(data: dd.Data) -> None:
    """Sidebar footer: when prices were last collected and where the data is read from."""
    runs = data.runs
    price_runs = runs[runs["kind"] == "prices"] if "kind" in runs else runs
    if not price_runs.empty:
        last = price_runs.sort_values("id").iloc[-1]
        when = pd.to_datetime(last["finished_at"] or last["started_at"])
        st.caption(f"Last price run: {when:%d %b %Y %H:%M} UTC, {last['status']} "
                   f"({int(last['products_ok'])} priced, {int(last['products_failed'])} failed).")
    st.caption(f"Data: {data.origin}.")
    if data.note:
        st.warning(data.note)


# --------------------------------------------------------------------------- the price-trend section
def render_trend(data: dd.Data, skus: list[str], *, key: str, count_label: str = "In this group",
                 brand_filter: bool = True, level: str | None = None) -> None:
    """Period and price-source controls, headline figures, trend chart and table for these laptops.

    key            prefix for widget keys (one per page, and per group where the brand list changes)
    brand_filter   show brand pills (off where the page already filters by brand)
    level          match level of the group these laptops form. The table shows a spec column only
                   for what that level ignores (touchscreen for relaxed, not for exact); None, for
                   laptops that are not a group, shows every spec column
    """
    anchor, dark = anchor_brand(), is_dark()
    brands = dd.brand_of(skus, data)
    names = dd.short_names(skus, data)
    all_brands = sorted(set(brands.values()), key=lambda b: (b.lower() != anchor.lower(), b))
    colors = charts.brand_colors(all_brands, dark)

    own_days = dd.own_day_count(data.prices)
    c_period, c_mode, c_brands = st.columns([3, 4, 5])
    period = c_period.segmented_control("Period", list(PERIODS), default="All", required=True, key=f"{key}-period")
    mode = c_mode.segmented_control(
        "Prices", [OWN_ONLY, MERGED], required=True, key=f"{key}-mode",
        # With under a week of first-hand history the backfill is switched on, or the chart would be a few dots.
        default=OWN_ONLY if own_days >= 7 else MERGED,
        help="Own scraper: prices this tracker read from Best Buy itself. Backfill: earlier days from "
             "Honey's price log, used only where there is no first-hand price.")
    shown_brands = all_brands
    if brand_filter:
        shown_brands = c_brands.pills("Brands", all_brands, selection_mode="multi", default=all_brands,
                                      key=f"{key}-brands") or []

    rows = dd.select_prices(data.prices, skus, "own" if mode == OWN_ONLY else "merged")
    price_full, source_full = dd.to_daily(rows)
    keep = [s for s in price_full.columns if brands[s] in shown_brands]
    price_full, source_full = price_full[keep], source_full[keep]
    if price_full.empty or price_full.dropna(how="all").empty:
        st.info("No prices for this selection.")
        return
    end = price_full.index.max()
    start = end - pd.Timedelta(days=PERIODS[period] - 1) if PERIODS[period] else price_full.index.min()
    price, source = price_full.loc[start:], source_full.loc[start:]
    if own_days < 7 and mode == MERGED:
        st.caption(f"First-hand history is {own_days} day{'s' if own_days != 1 else ''} long so far, "
                   "so the backfill is switched on by default.")

    # ---- headline figures
    current = dd.latest_prices(price)
    lows = dd.lowest_per_brand(price, source, brands)
    mine = current[[s for s in price.columns if brands[s] == anchor]].dropna()
    theirs = current[[s for s in price.columns if brands[s] != anchor]].dropna()
    k1, k2, k3, k4 = st.columns(4)
    tile = dict(border=True, height="stretch", delta_color="off")
    if mine.empty:
        k1.metric(f"{anchor} from", "–", **tile)
    else:
        delta, arrow = names[mine.idxmin()], "off"
        then = end - pd.Timedelta(days=30)
        if anchor in lows and then in lows[anchor].index and pd.notna(lows[anchor].at[then, "price"]):
            change = float(mine.min() - lows[anchor].at[then, "price"])
            if abs(change) < 0.005:
                delta = "no change in 30 days"
            else:
                delta, arrow = f"{'-' if change < 0 else '+'}${abs(change):,.2f} in 30 days", "auto"
        k1.metric(f"{anchor} from", charts.money(mine.min()), delta=delta, delta_arrow=arrow, **tile,
                  help=f"{names[mine.idxmin()]}: the lowest-priced {anchor} laptop here today.")
    if theirs.empty:
        k2.metric("Cheapest competitor", "–", **tile)
    else:
        k2.metric(f"Cheapest competitor · {brands[theirs.idxmin()]}", charts.money(theirs.min()),
                  delta=names[theirs.idxmin()], delta_arrow="off", **tile)
    if mine.empty or theirs.empty:
        k3.metric(f"{anchor} vs cheapest competitor", "–", **tile)
    else:
        gap = float(mine.min() - theirs.min())
        k3.metric(f"{anchor} vs cheapest competitor",
                  "$0.00" if abs(gap) < 0.005 else f"{MINUS if gap < 0 else '+'}${abs(gap):,.2f}",
                  delta="same price" if abs(gap) < 0.005 else f"{anchor} costs {'less' if gap < 0 else 'more'}",
                  delta_arrow="off", **tile, help="Lowest price on each side, today.")
    shown = sorted({brands[s] for s in price.columns})
    k4.metric(count_label, f"{price.shape[1]} laptops", delta=f"{len(shown)} brands", delta_arrow="off",
              **tile, help=", ".join(shown))

    # ---- trend chart
    view = st.segmented_control("View", [BY_BRAND, BY_LAPTOP], default=BY_BRAND, required=True,
                                label_visibility="collapsed", key=f"{key}-view")
    if view == BY_LAPTOP and price.shape[1] > MAX_LAPTOP_LINES:
        st.info(f"{price.shape[1]} laptops are too many to draw one line each (limit {MAX_LAPTOP_LINES}). "
                "Showing the lowest price per brand; narrow the selection to see every laptop.")
        view = BY_BRAND
    series = []
    if view == BY_BRAND:
        for brand in all_brands:
            if brand not in lows or lows[brand]["price"].dropna().empty:
                continue
            t = lows[brand]
            hover = [f"<b>{esc(brand)}</b>  {charts.money(p)}  ·  {esc(names.get(s, s))}  ·  "
                     f"{dd.SOURCE_LABEL.get(src, src)}" if pd.notna(p) else ""
                     for p, s, src in zip(t["price"], t["sku"], t["source"])]
            series.append({"group": brand, "color": colors[brand], "y": t["price"],
                           "own": t["source"].eq(dd.OWN), "hover": hover})
    else:
        for sku in sorted(price.columns, key=lambda s: (all_brands.index(brands[s]), names[s])):
            hover = [f"<b>{esc(brands[sku])} {esc(names[sku])}</b><br>{day:%a %d %b %Y}: {charts.money(p)}"
                     f"<br>{dd.SOURCE_LABEL.get(src, src)}" if pd.notna(p) else ""
                     for day, p, src in zip(price.index, price[sku], source[sku])]
            series.append({"group": brands[sku], "color": colors[brands[sku]], "y": price[sku],
                           "own": source[sku].eq(dd.OWN), "hover": hover})
    first_own = dd.first_own_day(data.prices)
    boundary = first_own if (mode == MERGED and first_own is not None
                             and price.index.min() < first_own <= price.index.max()) else None
    fig = charts.trend_figure(series, dark=dark, boundary=boundary, end_labels=view == BY_BRAND,
                              hovermode="x unified" if view == BY_BRAND else "closest")
    st.plotly_chart(fig, width="stretch", theme="streamlit", config={"displayModeBar": False}, key=f"{key}-chart",
                    alt="Price over time, one step line per brand or laptop")
    st.caption(("Each line is the lowest price among that brand's laptops shown here. "
                if view == BY_BRAND else
                "One line per laptop, coloured by brand; hover a line for the model. ")
               + "Solid line and dots: read from Best Buy by this tracker. Dashed: backfill from Honey's "
                 "price log. A price holds until it changes, so lines are steps; a gap is a day nobody observed.")

    # ---- table view of the same data
    summary = dd.summarise(price, price_full, rows)
    if summary.empty:
        return

    def spec(sku: str, field: str):
        return dd.value(data.specs, sku, field)

    summary["brand"] = summary["sku"].map(brands)
    summary["laptop"] = summary["sku"].map(names)
    summary["cpu"] = [spec(s, "cpu_name") for s in summary["sku"]]
    summary["memory"] = [spec(s, "memory_gb") for s in summary["sku"]]
    summary["storage"] = [dd.capacity_label(spec(s, "storage_gb")) for s in summary["sku"]]
    summary["storage_type"] = [dd.STORAGE_KIND.get(kind, kind) for kind in (spec(s, "storage_type") for s in summary["sku"])]
    summary["form"] = [str(spec(s, "form_factor") or "").capitalize() for s in summary["sku"]]
    # nullable types: a laptop with no value on file shows an empty cell, not "false" or "nan"
    summary["touchscreen"] = pd.array([spec(s, "touchscreen") for s in summary["sku"]], dtype="boolean")
    summary["screen_size"] = pd.array([spec(s, "screen_size_in") for s in summary["sku"]], dtype="Float64")
    summary["resolution"] = [spec(s, "resolution") for s in summary["sku"]]
    summary["url"] = [dd.value(data.products, s, "url") for s in summary["sku"]]
    summary["_anchor"] = summary["brand"].ne(anchor)
    summary = summary.sort_values(["_anchor", "price", "laptop"])
    # Spec columns follow the match level: only what the level ignores can differ between these laptops.
    columns = ["brand", "laptop"] + dd.differing_features(level) + [
        "price", "trend", "discount", "list_price", "low", "low_date", "high", "days_at_price", "resolution", "url"]
    st.dataframe(
        summary[columns], hide_index=True, width="stretch", key=f"{key}-table",
        alt="Per laptop: the specs these laptops may differ in, current price, trend, discount, lowest and highest price",
        column_config={
            "brand": st.column_config.TextColumn("Brand", pinned=True),
            "laptop": st.column_config.TextColumn("Laptop", pinned=True),
            "cpu": st.column_config.TextColumn("CPU"),
            "memory": st.column_config.NumberColumn("Memory", format="%d GB"),
            "storage": st.column_config.TextColumn("Storage"),
            "storage_type": st.column_config.TextColumn("Storage type"),
            "form": st.column_config.TextColumn("Form factor"),
            "touchscreen": st.column_config.CheckboxColumn("Touchscreen"),
            "screen_size": st.column_config.NumberColumn("Screen size", format='%.1f"'),
            "resolution": st.column_config.TextColumn("Resolution"),
            "price": st.column_config.NumberColumn("Price now", format="dollar"),
            "list_price": st.column_config.NumberColumn("List price", format="dollar",
                                                        help="Best Buy's regular price, from the own scraper."),
            "discount": st.column_config.NumberColumn("Off list", format="percent"),
            "low": st.column_config.NumberColumn("Lowest", format="dollar", help="Lowest price in the period."),
            "low_date": st.column_config.DateColumn("Last at lowest", format="D MMM YYYY"),
            "high": st.column_config.NumberColumn("Highest", format="dollar"),
            "days_at_price": st.column_config.NumberColumn("Days at this price"),
            "trend": st.column_config.LineChartColumn("Trend", help="Price over the period."),
            "url": st.column_config.LinkColumn("Best Buy", display_text="Open"),
        })
