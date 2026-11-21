"""Collection steps shared by the command-line scripts.

Each step writes to the local store first (see store.py); the caller mirrors the
store to Postgres afterwards when a database is configured.
"""
from __future__ import annotations

import datetime as dt
import statistics
import time

from .sources import bestbuy_graphql as bb
from .store import LocalStore, now_iso, today_utc


def new_run_id() -> int:
    """Run ids are made locally (epoch milliseconds) so a run logged offline keeps
    its id when it is replayed into Postgres."""
    return int(time.time() * 1000)


def resolve_brand(store: LocalStore, name: str) -> str:
    """'lenovo' -> 'Lenovo': the brands table holds Best Buy's exact facet spelling."""
    for b in store.brands():
        if b["name"].lower() == name.strip().lower():
            return b["name"]
    return name.strip()


def snapshot_rows(products: list[bb.Product], run_id: int) -> list[dict]:
    """First-hand price observations: one row per SKU that came back with a price."""
    day = today_utc()
    return [{"sku": p.sku, "snapshot_date": day, "price": p.price, "list_price": p.list_price,
             "in_stock": None, "source": bb.SOURCE, "captured_at": p.scraped_at or now_iso(),
             "run_id": run_id}
            for p in products if p.price is not None]


def flag_outliers(rows: list[dict], history: list[dict], *, window_days: int = 30) -> int:
    """Price sanity check. A first-hand price below 30% or above 300% of the SKU's
    30-day median is stored but flagged, so it is kept out of the charts until
    someone has looked at it. Needs at least three earlier days to judge."""
    today = dt.date.fromisoformat(today_utc())
    since = (today - dt.timedelta(days=window_days)).isoformat()
    earlier: dict[str, list[float]] = {}
    for r in history:
        if r["source"] == bb.SOURCE and since <= r["snapshot_date"] < today.isoformat() and not r.get("flagged"):
            earlier.setdefault(r["sku"], []).append(float(r["price"]))
    flagged = 0
    for row in rows:
        past = earlier.get(row["sku"]) or []
        if len(past) >= 3:
            median = statistics.median(past)
            if median > 0 and not 0.3 * median <= float(row["price"]) <= 3.0 * median:
                row["flagged"] = True
                flagged += 1
    return flagged


def rebuild_groups(store: LocalStore, anchor: str | None = None) -> tuple[list, list[dict]]:
    """Normalise the whole collection, regroup it and save the derived tables."""
    from . import grouping

    products = store.products()
    specs, groups = grouping.build(products, anchor)
    store.save_product_specs([s.to_dict() for s in specs])
    store.save_spec_groups(groups)
    return specs, groups


async def collect_brand(client: bb.BestBuyGQL, store: LocalStore, brand: str | None, *,
                        limit: int | None = None, marketplace: bool = False,
                        condition: str | None = "new", refresh_specs: bool = False) -> dict:
    """Crawl one brand's laptops (None = every brand) and save each product with its raw specs."""
    listed = await client.discover(brand, condition=condition, marketplace=marketplace, limit=limit)
    known = store.products()
    need = [p.sku for p in listed
            if refresh_specs or not (known.get(p.sku) or {}).get("raw_specs")]
    detailed, missing = await client.details(need) if need else ([], [])
    by_sku = {p.sku: p for p in detailed}

    records = []
    for p in listed:
        rec = p.to_dict()
        d = by_sku.get(p.sku)
        if d:
            rec["raw_specs"] = d.raw_specs
            rec["categories"] = d.categories
        records.append(rec)
    new, updated = store.upsert_products(records)
    return {"brand": brand or "ALL", "listed": len(listed), "new": new, "updated": updated,
            "specs_fetched": len(detailed), "specs_missing": missing, "products": listed}


async def collect_skus(client: bb.BestBuyGQL, store: LocalStore, skus: list[str], *,
                       pinned: bool = False) -> dict:
    """Add specific SKUs (with specs) through the same save path as a brand crawl."""
    detailed, missing = await client.details(skus)
    new, updated = store.upsert_products([p.to_dict() for p in detailed], pinned=pinned)
    return {"requested": len(skus), "new": new, "updated": updated, "missing": missing,
            "products": detailed}
