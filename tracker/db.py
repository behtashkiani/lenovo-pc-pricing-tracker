"""Postgres (Supabase) mirror of the local store.

Every script writes to the local store first and then calls `sync` here, so the
database is always a replayable copy: a failed or skipped sync loses nothing, the
next `python -m tracker.db sync` catches up. All statements are idempotent.

    python -m tracker.db check    # can we connect? what is in there?
    python -m tracker.db init     # create tables, views and read-only policies (sql/schema.sql)
    python -m tracker.db sync     # push the whole local store
    python -m tracker.db pull     # rebuild the local store from the database (new machine)

Connection string: SUPABASE_DB_URL in .env. On a network without IPv6 use the
"Session pooler" string from the Supabase dashboard (Connect), not the direct one:
the direct host db.<ref>.supabase.co is IPv6-only on the free plan.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter

from . import config
from .store import LocalStore

SCHEMA_FILE = config.ROOT / "sql" / "schema.sql"
TABLES = ("brands", "products", "specs", "groups", "runs", "snapshots")

_PRODUCT_COLS = ("sku", "bsin", "upc", "url", "title", "brand", "seller_class", "condition",
                 "open_box_condition", "categories", "raw_specs", "rank", "first_seen", "last_seen",
                 "pinned", "active", "miss_count")
_SPEC_COLS = ("sku", "brand", "model_family", "model_number", "cpu_model", "cpu_name", "cpu_class",
              "memory_gb", "storage_gb", "storage_type", "form_factor", "touchscreen", "os", "os_edition",
              "gpu", "gpu_name", "screen_size_in", "screen_bucket", "resolution", "color", "group_key",
              "strict_key", "relaxed_key", "class_key", "needs_review", "review_notes", "normaliser_ver")
_GROUP_COLS = ("group_key", "level", "label", "cpu", "memory_gb", "storage_gb", "storage_type",
               "form_factor", "touchscreen", "screen_bucket", "os", "gpu", "member_count", "brand_count",
               "competitor_count", "brands", "has_anchor", "tracked", "featured_rank", "created_at")
_RUN_COLS = ("id", "started_at", "finished_at", "source", "kind", "products_ok", "products_failed",
             "status", "error")
_SNAPSHOT_COLS = ("sku", "snapshot_date", "price", "list_price", "in_stock", "source", "flagged",
                  "captured_at", "run_id")
# One statement per chunk: each column travels as an array. No COPY and no prepared
# statement, so it works through either Supabase pooler mode.
_SNAPSHOT_CHUNK = 5000
_SNAPSHOT_TYPES = {"sku": str, "snapshot_date": str, "price": float, "list_price": float, "in_stock": bool,
                   "source": str, "flagged": bool, "captured_at": str, "run_id": int}
_SNAPSHOT_INSERT = (
    f"insert into price_snapshots ({', '.join(_SNAPSHOT_COLS)}) "
    "select * from unnest(%s::text[], %s::date[], %s::numeric[], %s::numeric[], %s::boolean[], "
    "%s::text[], %s::boolean[], %s::timestamptz[], %s::bigint[]) "
    "on conflict (sku, snapshot_date, source) do nothing")


def connect(url: str | None = None):
    """Open a connection. Works with the direct string and both pooler modes."""
    import psycopg

    if url is None:
        if not config.db_configured():
            raise RuntimeError("SUPABASE_DB_URL is not set (or still has the [YOUR-DATABASE-PASSWORD] "
                               "placeholder). Put the connection string in .env.")
        url = config.SUPABASE_DB_URL
    kwargs = {"connect_timeout": 20,
              # Prepared statements do not survive the transaction pooler (port 6543);
              # disabling them costs nothing here and makes either port work.
              "prepare_threshold": None}
    if "sslmode=" not in url and "supabase" in url:
        kwargs["sslmode"] = "require"
    return psycopg.connect(url, **kwargs)


def init(conn) -> None:
    conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
    conn.commit()


def _upsert(cur, table: str, cols: tuple[str, ...], key: str, rows: list[tuple], update: str | None = None) -> None:
    if not rows:
        return
    placeholders = ", ".join(["%s"] * len(cols))
    if update is None:
        update = ", ".join(f"{c} = excluded.{c}" for c in cols if c != key)
    cur.executemany(f"insert into {table} ({', '.join(cols)}) values ({placeholders}) "
                    f"on conflict ({key}) do update set {update}", rows)


def sync(store: LocalStore | None = None, tables: tuple[str, ...] = TABLES, conn=None) -> dict[str, int]:
    """Push the local store into Postgres. Returns rows handled per table."""
    from psycopg.types.json import Jsonb

    store = store or LocalStore()
    own = conn is None
    conn = conn or connect()
    done: dict[str, int] = {}
    try:
        with conn.cursor() as cur:
            if "brands" in tables:
                rows = [(b["name"], bool(b.get("enabled", True))) for b in store.brands()]
                _upsert(cur, "brands", ("name", "enabled"), "name", rows)
                done["brands"] = len(rows)

            products = store.products() if {"products", "specs", "snapshots"} & set(tables) else {}
            if "products" in tables:
                rows = []
                for p in products.values():
                    row = {c: p.get(c) for c in _PRODUCT_COLS}
                    row["raw_specs"] = Jsonb(p["raw_specs"]) if p.get("raw_specs") is not None else None
                    row["categories"] = p.get("categories") or []
                    row["miss_count"] = p.get("miss_count") or 0
                    row["url"] = p.get("url") or f"https://www.bestbuy.com/site/{p['sku']}.p?skuId={p['sku']}"
                    row["title"] = p.get("title") or ""
                    row["seller_class"] = p.get("seller_class") or "1P"
                    row["condition"] = p.get("condition") or "new"
                    row["pinned"] = bool(p.get("pinned"))
                    row["active"] = bool(p.get("active", True))
                    rows.append(tuple(row[c] for c in _PRODUCT_COLS))
                update = ", ".join(
                    "first_seen = least(products.first_seen, excluded.first_seen)" if c == "first_seen"
                    else "raw_specs = coalesce(excluded.raw_specs, products.raw_specs)" if c == "raw_specs"
                    else f"{c} = excluded.{c}"
                    for c in _PRODUCT_COLS if c != "sku")
                _upsert(cur, "products", _PRODUCT_COLS, "sku", rows, update)
                done["products"] = len(rows)

            if "specs" in tables:
                specs = [s for s in store.product_specs().values() if s["sku"] in products]
                rows = [tuple((s.get(c) or []) if c == "review_notes" else bool(s.get(c)) if c == "needs_review"
                              else s.get(c) for c in _SPEC_COLS) for s in specs]
                update = ", ".join(f"{c} = excluded.{c}" for c in _SPEC_COLS if c != "sku") + ", updated_at = now()"
                _upsert(cur, "product_specs", _SPEC_COLS, "sku", rows, update)
                cur.execute("delete from product_specs where not (sku = any(%s))", ([s["sku"] for s in specs],))
                done["specs"] = len(rows)

            if "groups" in tables:
                groups = store.spec_groups()
                cur.execute("delete from spec_groups")      # generated table: rewritten in full
                placeholders = ", ".join(["%s"] * len(_GROUP_COLS))
                cur.executemany(f"insert into spec_groups ({', '.join(_GROUP_COLS)}) values ({placeholders})",
                                [tuple((g.get(c) or []) if c == "brands" else g.get(c) for c in _GROUP_COLS)
                                 for g in groups])
                done["groups"] = len(groups)

            if "runs" in tables:
                rows = [tuple(r.get(c) for c in _RUN_COLS) for r in store.runs()]
                _upsert(cur, "scrape_runs", _RUN_COLS, "id", rows)
                done["runs"] = len(rows)

            if "snapshots" in tables:
                snaps = [r for r in store.snapshots() if r["sku"] in products]
                # Only (source, day) slices the database is missing rows for are sent, so the
                # daily sync uploads one day of prices, not the whole history.
                cur.execute("select source, snapshot_date::text, count(*) from price_snapshots group by 1, 2")
                have = {(source, day): n for source, day, n in cur.fetchall()}
                local = Counter((r["source"], r["snapshot_date"]) for r in snaps)
                todo = [r for r in snaps
                        if local[(r["source"], r["snapshot_date"])] > have.get((r["source"], r["snapshot_date"]), 0)]
                inserted = 0
                for i in range(0, len(todo), _SNAPSHOT_CHUNK):
                    chunk = todo[i:i + _SNAPSHOT_CHUNK]
                    # one Python type per column: psycopg cannot send an array mixing 399 and 399.99
                    columns = [[_SNAPSHOT_TYPES[c](r[c]) if r.get(c) is not None else (False if c == "flagged" else None)
                                for r in chunk] for c in _SNAPSHOT_COLS]
                    cur.execute(_SNAPSHOT_INSERT, columns)
                    inserted += cur.rowcount
                done["snapshots"] = inserted
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        if own:
            conn.close()
    return done


def sync_safely(store: LocalStore, tables: tuple[str, ...] = TABLES) -> bool:
    """Sync at the end of a script. A database problem must never fail the run:
    the data is already in the local store and the next sync replays it."""
    if not config.db_configured():
        print("database not configured: saved to the local store only "
              "(set SUPABASE_DB_URL in .env, then run: python -m tracker.db init && python -m tracker.db sync)")
        return False
    try:
        done = sync(store, tables)
    except Exception as e:  # noqa: BLE001 - any failure here is reported, never raised
        print(f"database sync failed ({type(e).__name__}: {str(e).strip()[:200]}); "
              "the data is safe in the local store, run `python -m tracker.db sync` later", file=sys.stderr)
        return False
    print("database synced: " + ", ".join(f"{k} {v}" for k, v in done.items()))
    return True


def pull(store: LocalStore | None = None, conn=None) -> dict[str, int]:
    """Rebuild the local store from Postgres (brands, products, prices, run log).
    Specs and groups are derived data: run build_groups.py afterwards."""
    from psycopg.rows import dict_row

    store = store or LocalStore()
    own = conn is None
    conn = conn or connect()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("select name, enabled from brands order by name")
            brands = cur.fetchall()
            if brands:
                store.save_brands(brands)
            cur.execute(f"select {', '.join(_PRODUCT_COLS)} from products")
            products = {}
            for p in cur.fetchall():
                for c in ("first_seen", "last_seen"):
                    p[c] = p[c].isoformat(timespec="seconds") if p[c] else None
                products[p["sku"]] = p
            store._save_products(products)
            cur.execute(f"select {', '.join(_SNAPSHOT_COLS)} from price_snapshots order by snapshot_date, sku, source")
            snaps = []
            for r in cur.fetchall():
                r["snapshot_date"] = r["snapshot_date"].isoformat()
                r["price"] = float(r["price"])
                r["list_price"] = float(r["list_price"]) if r["list_price"] is not None else None
                r["captured_at"] = r["captured_at"].isoformat(timespec="seconds") if r["captured_at"] else None
                snaps.append(r)
            inserted = store.insert_snapshots(snaps)
            cur.execute(f"select {', '.join(_RUN_COLS)} from scrape_runs order by id")
            known = {r["id"] for r in store.runs()}
            runs = 0
            for r in cur.fetchall():
                if r["id"] in known:
                    continue
                for c in ("started_at", "finished_at"):
                    r[c] = r[c].isoformat(timespec="seconds") if r[c] else None
                store.log_run(r)
                runs += 1
    finally:
        if own:
            conn.close()
    return {"brands": len(brands), "products": len(products), "snapshots": inserted, "runs": runs}


def check(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("select current_user, version()")
        user, version = cur.fetchone()
        print(f"connected as {user}: {version.split(',')[0]}")
        cur.execute("select count(*) from information_schema.tables where table_schema = 'public' "
                    "and table_name = 'price_snapshots'")
        if not cur.fetchone()[0]:
            print("schema not created yet: run `python -m tracker.db init`")
            return
        for table in ("brands", "products", "product_specs", "spec_groups", "price_snapshots", "scrape_runs"):
            cur.execute(f"select count(*) from {table}")
            print(f"  {table:<16}{cur.fetchone()[0]:>8} rows")
        cur.execute("select source, count(*), min(snapshot_date), max(snapshot_date) from price_snapshots "
                    "group by source order by source")
        for source, n, first, last in cur.fetchall():
            print(f"  prices from {source:<18}{n:>8} rows  {first} .. {last}")
        cur.execute("select count(*) from tracked_skus")
        print(f"  tracked_skus    {cur.fetchone()[0]:>8}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["check", "init", "sync", "pull"])
    args = ap.parse_args()
    try:
        conn = connect()
    except Exception as e:  # noqa: BLE001
        print(f"cannot connect: {type(e).__name__}: {str(e).strip()[:300]}", file=sys.stderr)
        if "getaddrinfo" in str(e) or "resolve" in str(e).lower() or "Name or service" in str(e):
            print("The direct host db.<ref>.supabase.co is IPv6-only on the free plan. Copy the "
                  "'Session pooler' connection string from the Supabase dashboard (Connect) into "
                  "SUPABASE_DB_URL instead.", file=sys.stderr)
        return 2
    with conn:
        if args.command == "check":
            check(conn)
        elif args.command == "init":
            init(conn)
            print("schema applied")
            check(conn)
        elif args.command == "sync":
            print("synced: " + ", ".join(f"{k} {v}" for k, v in sync(conn=conn).items()))
        elif args.command == "pull":
            print("pulled: " + ", ".join(f"{k} {v}" for k, v in pull(conn=conn).items()))
            print("now run: python build_groups.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
