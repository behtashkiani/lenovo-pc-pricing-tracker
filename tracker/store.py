"""Local JSON store.

Two jobs:

1. Offline mode. With no database configured every script works against these
   files, so the collection, the groups and the daily prices can be built and
   inspected before Supabase is connected.
2. Journal. Every script writes here first and mirrors to Postgres afterwards.
   A day the scraper ran is therefore never lost to a database outage:
   `python -m tracker.db sync` replays the files into Postgres, idempotently.

Files (under TRACKER_DATA_DIR, default ./data):

    brands.json            the brands table, maintained by hand
    products.jsonl         one collected SKU per line, with its untouched spec table
    product_specs.jsonl    normalised fields + group keys, rewritten by build_groups
    spec_groups.json       generated spec groups, rewritten by build_groups
    price_snapshots.jsonl  append-only; unique on (sku, snapshot_date, source)
    scrape_runs.jsonl      append-only run log
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import config

DEFAULT_BRANDS = ["Lenovo", "HP", "Dell", "ASUS", "Acer", "Microsoft", "Samsung",
                  "MSI", "LG", "GIGABYTE", "Alienware", "HP OMEN"]

# Fields of a product that a later crawl may refresh. Everything else (first_seen,
# pinned) is owned by the store.
_REFRESHED = ("bsin", "upc", "url", "title", "brand", "seller_class", "condition",
              "open_box_condition", "categories", "rank")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _dump_jsonl(rows: list[dict]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in rows)


class LocalStore:
    def __init__(self, root: Path | str | None = None):
        self.root = Path(root) if root else config.DATA_DIR
        self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, name: str) -> Path:
        return self.root / name

    # ---- brands
    def brands(self) -> list[dict]:
        path = self._p("brands.json")
        if not path.exists():
            self.save_brands([{"name": b, "enabled": True} for b in DEFAULT_BRANDS])
        return json.loads(path.read_text(encoding="utf-8"))

    def save_brands(self, brands: list[dict]) -> None:
        _write_atomic(self._p("brands.json"), json.dumps(brands, indent=1) + "\n")

    def add_brand(self, name: str) -> None:
        brands = self.brands()
        if name.lower() not in {b["name"].lower() for b in brands}:
            brands.append({"name": name, "enabled": True})
            self.save_brands(brands)

    # ---- products
    def products(self) -> dict[str, dict]:
        return {r["sku"]: r for r in _read_jsonl(self._p("products.jsonl"))}

    def upsert_products(self, incoming: list[dict], *, pinned: bool = False) -> tuple[int, int]:
        """Merge collected products. Returns (new, updated)."""
        current = self.products()
        stamp = now_iso()
        new = updated = 0
        for p in incoming:
            sku = str(p["sku"])
            old = current.get(sku)
            if old is None:
                rec = {"sku": sku, "first_seen": stamp, "pinned": False, "active": True, "raw_specs": None}
                new += 1
            else:
                rec = old
                updated += 1
            for k in _REFRESHED:
                if p.get(k) is not None or k not in rec:
                    rec[k] = p.get(k)
            if p.get("raw_specs"):          # a listing-only refresh must not wipe the spec table
                rec["raw_specs"] = p["raw_specs"]
            rec["last_seen"] = stamp
            rec["active"] = True
            rec["pinned"] = bool(rec.get("pinned")) or pinned
            current[sku] = rec
        self._save_products(current)
        return new, updated

    def record_misses(self, found: list[str], missing: list[str], limit: int = 3) -> list[str]:
        """Count consecutive not-found answers per SKU. A SKU the gateway no longer
        returns `limit` times in a row is marked inactive (its history is kept).
        Returns the SKUs deactivated by this call."""
        current = self.products()
        dropped = []
        for sku in found:
            if sku in current and current[sku].get("miss_count"):
                current[sku]["miss_count"] = 0
        for sku in missing:
            p = current.get(sku)
            if not p:
                continue
            p["miss_count"] = int(p.get("miss_count") or 0) + 1
            if p["miss_count"] >= limit and p.get("active", True):
                p["active"] = False
                dropped.append(sku)
        self._save_products(current)
        return dropped

    def _save_products(self, by_sku: dict[str, dict]) -> None:
        _write_atomic(self._p("products.jsonl"), _dump_jsonl([by_sku[k] for k in sorted(by_sku)]))

    # ---- derived tables (rewritten in full by build_groups)
    def product_specs(self) -> dict[str, dict]:
        return {r["sku"]: r for r in _read_jsonl(self._p("product_specs.jsonl"))}

    def save_product_specs(self, specs: list[dict]) -> None:
        _write_atomic(self._p("product_specs.jsonl"), _dump_jsonl(sorted(specs, key=lambda r: r["sku"])))

    def spec_groups(self) -> list[dict]:
        path = self._p("spec_groups.json")
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

    def save_spec_groups(self, groups: list[dict]) -> None:
        _write_atomic(self._p("spec_groups.json"), json.dumps(groups, indent=1, ensure_ascii=False) + "\n")

    def tracked_skus(self) -> list[str]:
        """Active, New, Best Buy-sold members of tracked groups (at any match level),
        plus pinned products. Mirrors the tracked_skus view in sql/schema.sql."""
        tracked_groups = {g["group_key"] for g in self.spec_groups() if g.get("tracked")}
        specs = self.product_specs()
        out = []
        for sku, p in self.products().items():
            if not p.get("active", True):
                continue
            s = specs.get(sku) or {}
            in_group = bool(tracked_groups & {s.get("strict_key"), s.get("group_key"), s.get("relaxed_key"),
                                              s.get("class_key")})
            eligible = p.get("seller_class") == "1P" and (p.get("condition") or "new") == "new"
            if p.get("pinned") or (in_group and eligible):
                out.append(sku)
        return sorted(out)

    # ---- prices
    def snapshots(self) -> list[dict]:
        return _read_jsonl(self._p("price_snapshots.jsonl"))

    def insert_snapshots(self, rows: list[dict]) -> int:
        """Append price rows, skipping any (sku, snapshot_date, source) already present."""
        seen = {(r["sku"], r["snapshot_date"], r["source"]) for r in self.snapshots()}
        fresh = []
        for r in rows:
            key = (str(r["sku"]), r["snapshot_date"], r["source"])
            if r.get("price") is None or key in seen:
                continue
            seen.add(key)
            list_price = r.get("list_price")
            fresh.append({"sku": key[0], "snapshot_date": key[1], "price": float(r["price"]),
                          "list_price": float(list_price) if list_price is not None else None,
                          "in_stock": r.get("in_stock"),
                          "source": key[2], "flagged": bool(r.get("flagged")),
                          "captured_at": r.get("captured_at") or now_iso(),
                          "run_id": r.get("run_id")})
        if fresh:
            self.root.mkdir(parents=True, exist_ok=True)
            with open(self._p("price_snapshots.jsonl"), "a", encoding="utf-8") as f:
                f.write(_dump_jsonl(fresh))
        return len(fresh)

    # ---- run log
    def runs(self) -> list[dict]:
        return _read_jsonl(self._p("scrape_runs.jsonl"))

    def log_run(self, run: dict) -> None:
        with open(self._p("scrape_runs.jsonl"), "a", encoding="utf-8") as f:
            f.write(_dump_jsonl([run]))
