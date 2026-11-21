"""Data layer of the dashboard.

Loads the collection and the prices (from Supabase when it is configured and
reachable, otherwise from the local store) and shapes them for the charts.
There is no Streamlit in this module, so everything in it is testable.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from . import config
from .normalise import LEVEL_FIELDS, canonical_brand, cpu_class_label, gpu_label
from .store import LocalStore

OWN = "bestbuy_graphql"

# Which source wins when several have a price for the same laptop and day.
PRIORITY = {OWN: 1, "honey": 2, "honey_carried": 3, "shopsavvy": 4, "shopsavvy_carried": 5}

SOURCE_LABEL = {
    OWN: "own scraper",
    "honey": "Honey, recorded change",
    "honey_carried": "Honey, carried forward",
    "shopsavvy": "ShopSavvy, observed",
    "shopsavvy_carried": "ShopSavvy, carried forward",
}
# Collection method behind each source label.
METHOD = {OWN: "Own scraper (Best Buy)", "honey": "Honey", "honey_carried": "Honey",
          "shopsavvy": "ShopSavvy", "shopsavvy_carried": "ShopSavvy"}

_PRODUCT_COLS = ["sku", "title", "brand", "url", "seller_class", "condition", "pinned", "active"]
_PRICE_COLS = ["sku", "date", "price", "list_price", "source", "flagged"]


@dataclass
class Data:
    origin: str                 # "Supabase" | "local store"
    products: pd.DataFrame      # index: sku
    specs: pd.DataFrame         # index: sku (normalised fields and group keys)
    groups: pd.DataFrame        # one row per group and match level; `skus` and `brands` are lists
    prices: pd.DataFrame        # sku, date, price, list_price, source, flagged
    runs: pd.DataFrame
    note: str | None = None


# --------------------------------------------------------------------------- loading
def _prices_frame(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return pd.DataFrame({"sku": pd.Series(dtype=str), "date": pd.Series(dtype="datetime64[ns]"),
                             "price": pd.Series(dtype=float), "list_price": pd.Series(dtype=float),
                             "source": pd.Series(dtype=str), "flagged": pd.Series(dtype=bool)})
    df = rows.copy()
    df["sku"] = df["sku"].astype(str)
    df["date"] = pd.to_datetime(df["snapshot_date"])
    df["price"] = pd.to_numeric(df["price"], errors="coerce").astype(float)
    df["list_price"] = (pd.to_numeric(df["list_price"], errors="coerce").astype(float)
                        if "list_price" in df else float("nan"))
    df["flagged"] = df["flagged"].eq(True) if "flagged" in df else False
    return df[_PRICE_COLS].dropna(subset=["price"]).reset_index(drop=True)


def load_local(store: LocalStore | None = None) -> Data:
    store = store or LocalStore()
    products = pd.DataFrame([{c: p.get(c) for c in _PRODUCT_COLS} for p in store.products().values()],
                            columns=_PRODUCT_COLS).set_index("sku")
    specs = pd.DataFrame(list(store.product_specs().values()))
    specs = specs.set_index("sku") if not specs.empty else pd.DataFrame(index=pd.Index([], name="sku"))
    return Data("local store", products, specs, pd.DataFrame(store.spec_groups()),
                _prices_frame(pd.DataFrame(store.snapshots())), pd.DataFrame(store.runs()))


def load_db() -> Data:
    from . import db

    def frame(cur, sql: str) -> pd.DataFrame:
        cur.execute(sql)
        return pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])

    conn = db.connect()
    try:
        with conn.cursor() as cur:
            products = frame(cur, f"select {', '.join(_PRODUCT_COLS)} from products").set_index("sku")
            specs = frame(cur, "select * from product_specs").set_index("sku")
            groups = frame(cur, "select * from spec_groups")
            members = frame(cur, "select group_key, array_agg(sku order by sku) as skus "
                                 "from group_members group by group_key")
            prices = frame(cur, "select sku, snapshot_date, price, list_price, source, flagged from price_snapshots")
            runs = frame(cur, "select * from scrape_runs order by id")
    finally:
        conn.close()
    groups = groups.merge(members, on="group_key", how="left")
    groups["skus"] = groups["skus"].apply(lambda v: list(v) if isinstance(v, (list, tuple)) else [])
    if "screen_size_in" in specs:
        specs["screen_size_in"] = pd.to_numeric(specs["screen_size_in"], errors="coerce")
    return Data("Supabase", products, specs, groups, _prices_frame(prices), runs)


def load(prefer: str = "auto") -> Data:
    """prefer: 'auto' (database when configured and reachable, else local), 'local' or 'db'."""
    if prefer != "local" and config.db_configured():
        try:
            return load_db()
        except Exception as e:  # noqa: BLE001 - the dashboard must still open on the local store
            if prefer == "db":
                raise
            data = load_local()
            data.note = f"Supabase could not be read ({type(e).__name__}); showing the local store."
            return data
    return load_local()


# --------------------------------------------------------------------------- lookups
def value(df: pd.DataFrame, sku: str, col: str):
    """A cell, or None when the row, the column or the value is missing."""
    if sku not in df.index or col not in df.columns:
        return None
    v = df.at[sku, col]
    if isinstance(v, (list, dict, tuple)):
        return v
    return None if v is None or pd.isna(v) else v


def brand_of(skus: list[str], data: Data) -> dict[str, str]:
    """Maker of each laptop (sub-brands folded in), as used for grouping."""
    return {s: value(data.specs, s, "brand") or canonical_brand(value(data.products, s, "brand")) or "?"
            for s in skus}


def short_names(skus: list[str], data: Data) -> dict[str, str]:
    """A short, unique display name per laptop: model line and screen size."""
    names: dict[str, str] = {}
    for sku in skus:
        family = value(data.specs, sku, "model_family")
        size = value(data.specs, sku, "screen_size_in")
        size_text = f'{float(size):g}"' if size is not None else ""
        if not family:
            title = str(value(data.products, sku, "title") or sku)
            parts = [p.strip() for p in title.split(" - ")]
            family = parts[1] if len(parts) > 1 else parts[0]
            names[sku] = family[:48]
        else:
            names[sku] = f"{family} {size_text}".strip()
    # same model in two colours, or two listings of one configuration
    for extra in ("color", None):
        seen: dict[str, list[str]] = {}
        for sku, name in names.items():
            seen.setdefault(name, []).append(sku)
        for name, same in seen.items():
            if len(same) > 1:
                for sku in same:
                    tag = value(data.specs, sku, extra) if extra else sku
                    if tag:
                        names[sku] = f"{name} · {tag}"
    return names


STORAGE_KIND = {"ssd": "SSD", "emmc": "eMMC", "ufs": "UFS", "hdd": "HDD", "hybrid": "SSD+HDD"}


def capacity_label(gb, kind: str | None = None) -> str:
    """512 -> '512 GB', 1024 -> '1 TB'; with the drive type when given."""
    if gb is None or pd.isna(gb):
        return ""
    gb = int(gb)
    text = f"{gb // 1024} TB" if gb >= 1024 and gb % 1024 == 0 else f"{gb} GB"
    return f"{text} {STORAGE_KIND.get(kind or '', '')}".strip()


# Spec columns of the per-laptop table, in display order, and the field a match level
# must compare for the column to say nothing (every laptop of the group has one value).
FEATURE_FIELDS = {"cpu": "cpu_model", "memory": "memory_gb", "storage": "storage_gb",
                  "storage_type": "storage_type", "form": "form_factor", "touchscreen": "touchscreen",
                  "screen_size": "screen_bucket"}


def differing_features(level: str | None) -> list[str]:
    """Spec columns worth showing for a group of this match level: the features the level
    ignores, in which its laptops may therefore differ. None (no group, any set of laptops)
    gives every feature.

        strict   nothing
        exact    screen_size
        relaxed  storage_type, touchscreen, screen_size
        class    cpu as well
    """
    compared = set(LEVEL_FIELDS[level]) if level else set()
    return [feature for feature, field in FEATURE_FIELDS.items() if field not in compared]


# --------------------------------------------------------------------------- spec explorer
_SPEC_COLS = ["brand", "cpu_model", "cpu_name", "cpu_class", "memory_gb", "storage_gb", "storage_type",
              "form_factor", "touchscreen", "os", "gpu", "screen_size_in", "resolution"]


def spec_table(data: Data) -> pd.DataFrame:
    """One row per laptop that can be filtered by spec: New, sold by Best Buy and fully
    normalised. Adds display columns (cpu_family, graphics) and the latest known price."""
    p = data.products
    sold = p[p["active"].ne(False) & p["seller_class"].eq("1P") & p["condition"].fillna("new").eq("new")].index
    specs = data.specs.loc[data.specs.index.intersection(sold)]
    if specs.empty or "needs_review" not in specs:
        return pd.DataFrame(columns=_SPEC_COLS + ["cpu_family", "graphics", "price"])
    table = specs.loc[~specs["needs_review"].eq(True), [c for c in _SPEC_COLS if c in specs]].copy()
    table["cpu_family"] = [cpu_class_label(str(cls)) if isinstance(cls, str) and cpu_class_label(cls) else name
                           for cls, name in zip(table["cpu_class"], table["cpu_name"])]
    table["graphics"] = ["Integrated" if g == "integrated" else gpu_label(str(g)) for g in table["gpu"]]
    last = select_prices(data.prices, table.index, "merged").sort_values("date").groupby("sku")["price"].last()
    table["price"] = last.reindex(table.index)
    return table


def filter_specs(table: pd.DataFrame, *, brands=None, cpu_families=None, cpus=None, memory=None, storage=None,
                 storage_types=None, form_factors=None, touch=None, os=None, graphics=None, screen=None,
                 price=None) -> pd.DataFrame:
    """Rows matching every given criterion. A list means "any of these"; a (low, high)
    pair is an inclusive range; None or an empty list means "no constraint"."""
    keep = pd.Series(True, index=table.index)
    for column, wanted in (("brand", brands), ("cpu_family", cpu_families), ("cpu_name", cpus),
                           ("storage_type", storage_types), ("form_factor", form_factors), ("os", os),
                           ("graphics", graphics)):
        if wanted:
            keep &= table[column].isin(list(wanted))
    for column, bounds in (("memory_gb", memory), ("storage_gb", storage), ("screen_size_in", screen),
                           ("price", price)):
        if bounds:
            low, high = bounds
            keep &= pd.to_numeric(table[column], errors="coerce").between(low, high)
    if touch is not None:
        keep &= table["touchscreen"].eq(bool(touch))
    return table[keep]


# --------------------------------------------------------------------------- series
def select_prices(prices: pd.DataFrame, skus, mode: str = "merged") -> pd.DataFrame:
    """One row per laptop and day for these laptops.

    mode 'own'     first-hand prices only (read from Best Buy by this tracker)
    mode 'merged'  the most trusted source of each day (PRIORITY); flagged rows never appear
    """
    df = prices[prices["sku"].isin(list(skus)) & ~prices["flagged"]]
    if mode == "own":
        df = df[df["source"] == OWN]
    df = df.assign(_rank=df["source"].map(PRIORITY)).dropna(subset=["_rank"])
    df = df.sort_values(["sku", "date", "_rank"]).drop_duplicates(["sku", "date"], keep="first")
    return df.drop(columns="_rank").reset_index(drop=True)


def to_daily(rows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(price, source) tables: one row per calendar day, one column per laptop.
    A day without a price stays empty: missing days are shown as gaps, never invented."""
    if rows.empty:
        empty = pd.DataFrame(index=pd.DatetimeIndex([], name="date"))
        return empty, empty.copy()
    days = pd.date_range(rows["date"].min(), rows["date"].max(), freq="D", name="date")
    price = rows.pivot(index="date", columns="sku", values="price").reindex(days)
    source = rows.pivot(index="date", columns="sku", values="source").reindex(days)
    return price, source


def lowest_per_brand(price: pd.DataFrame, source: pd.DataFrame, brands: dict[str, str]) -> dict[str, pd.DataFrame]:
    """Per brand and day: the lowest price among its laptops, which laptop it was, and its source."""
    out: dict[str, pd.DataFrame] = {}
    for brand in sorted({brands[s] for s in price.columns}):
        cols = [s for s in price.columns if brands[s] == brand]
        sub = price[cols]
        low = sub.min(axis=1, skipna=True)
        has = low.notna()
        which = pd.Series(None, index=sub.index, dtype=object)
        if has.any():
            which[has] = sub[has].idxmin(axis=1)
        src = pd.Series([source.at[day, sku] if isinstance(sku, str) else None for day, sku in which.items()],
                        index=sub.index, dtype=object)
        out[brand] = pd.DataFrame({"price": low, "sku": which, "source": src})
    return out


def days_at_current_price(series: pd.Series) -> int | None:
    """How many consecutive days, ending at the last observed day, the price has been what it is now."""
    known = series.dropna()
    if known.empty:
        return None
    day, current = known.index[-1], known.iloc[-1]
    days = 0
    while day in series.index and pd.notna(series[day]) and abs(series[day] - current) < 0.005:
        days += 1
        day -= pd.Timedelta(days=1)
    return days


def latest_prices(price: pd.DataFrame, max_age_days: int = 3) -> pd.Series:
    """Current price per laptop: its last observed price, unless that is older than
    `max_age_days` before the end of the table (a laptop that stopped being priced)."""
    if price.empty:
        return pd.Series(dtype=float)
    cutoff = price.index.max() - pd.Timedelta(days=max_age_days)
    recent = price[price.index >= cutoff]
    return recent.ffill().iloc[-1]


def summarise(price: pd.DataFrame, price_full: pd.DataFrame, rows: pd.DataFrame) -> pd.DataFrame:
    """One row per laptop for the selected period.

    price       daily prices inside the period
    price_full  the whole series (for "days at current price")
    rows        the long rows behind them (for the list price, which only first-hand rows carry)
    """
    listed = (rows[rows["source"] == OWN].dropna(subset=["list_price"]).sort_values("date")
              .groupby("sku")["list_price"].last())
    out = []
    for sku in price.columns:
        s = price[sku].dropna()
        if s.empty:
            continue
        current, low, high = float(s.iloc[-1]), float(s.min()), float(s.max())
        list_price = float(listed[sku]) if sku in listed.index else None
        out.append({
            "sku": sku,
            "price": current,
            "as_of": s.index[-1],
            "list_price": list_price,
            "discount": (1 - current / list_price) if list_price and list_price > current else 0.0 if list_price else None,
            "low": low,
            "low_date": s[s <= low + 0.005].index[-1],
            "high": high,
            "days_at_price": days_at_current_price(price_full[sku]) if sku in price_full.columns else None,
            "trend": [float(v) for v in price[sku].ffill().dropna()],
        })
    return pd.DataFrame(out)


def source_coverage(prices: pd.DataFrame) -> pd.DataFrame:
    """Per collection method and source label: rows, laptops covered, first and last day."""
    if prices.empty:
        return pd.DataFrame(columns=["method", "source", "rows", "laptops", "first", "last"])
    g = prices[~prices["flagged"]].groupby("source")
    table = g.agg(rows=("price", "size"), laptops=("sku", "nunique"), first=("date", "min"),
                  last=("date", "max")).reset_index()
    table["method"] = table["source"].map(METHOD).fillna(table["source"])
    table["_rank"] = table["source"].map(PRIORITY).fillna(99)
    return table.sort_values("_rank")[["method", "source", "rows", "laptops", "first", "last"]].reset_index(drop=True)


def first_own_day(prices: pd.DataFrame):
    own = prices[(prices["source"] == OWN) & ~prices["flagged"]]
    return own["date"].min() if not own.empty else None


def own_day_count(prices: pd.DataFrame) -> int:
    return int(prices.loc[(prices["source"] == OWN) & ~prices["flagged"], "date"].nunique())
