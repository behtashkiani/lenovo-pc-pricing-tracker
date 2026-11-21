"""Database layer against a real PostgreSQL server.

Skipped unless TEST_DATABASE_URL points at a disposable database (never the
production one: the test creates the schema and writes rows), e.g.

    TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:54329/postgres pytest tests/test_db_integration.py
"""
import os

import pytest
from conftest import make_product

from tracker import collect, db
from tracker.store import LocalStore

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")

WIN = "Windows 11 Home"


def _laptop(brand, sku, **kw):
    spec = dict(cpu_brand="Intel", series="Intel Core Ultra 5 Series 2", number="226V", ram="16 gigabytes",
                storage="512 gigabytes", storage_type="SSD", graphics="Intel Arc", graphics_type="Integrated",
                os=WIN, touch="true", two_in_one="true", size="16 inches", resolution="1920 x 1200 (WUXGA)")
    spec.update(kw)
    p = make_product(f'{brand} - 2-in-1 16" Touchscreen Laptop - Intel Core Ultra 5 226V - 16GB Memory - 512GB SSD',
                     brand, sku=sku, **spec)
    p.update(url=f"https://www.bestbuy.com/site/{sku}.p?skuId={sku}", upc="1" * 12, bsin="ABCDEFGH12",
             categories=["Windows Laptops"], rank=1)
    return p


@pytest.fixture
def store(tmp_path):
    s = LocalStore(tmp_path / "data")
    s.brands()
    s.upsert_products([_laptop("Lenovo", "6000001"), _laptop("HP", "6000002"),
                       _laptop("Dell", "6000003", ram="8 gigabytes"),               # different memory: its own group
                       _laptop("HP", "16000004", seller_class="3P")])               # Marketplace: never counted
    collect.rebuild_groups(s, "Lenovo")
    s.insert_snapshots([
        {"sku": "6000001", "snapshot_date": "2026-11-18", "price": 700.00, "source": "honey"},
        {"sku": "6000001", "snapshot_date": "2026-11-19", "price": 655.00, "source": "honey_carried"},
        {"sku": "6000001", "snapshot_date": "2026-11-19", "price": 649.99, "list_price": 999.99,
         "source": "bestbuy_graphql", "run_id": 1},
        {"sku": "6000002", "snapshot_date": "2026-11-19", "price": 9.99, "source": "bestbuy_graphql",
         "flagged": True, "run_id": 1},
        {"sku": "6000002", "snapshot_date": "2026-11-19", "price": 999.99, "source": "honey"},
    ])
    s.log_run({"id": 1, "started_at": "2026-11-19T09:00:00+00:00", "finished_at": "2026-11-19T09:01:00+00:00",
               "source": "bestbuy_graphql", "kind": "prices", "products_ok": 2, "products_failed": 0,
               "status": "ok", "error": None})
    return s


@pytest.fixture
def conn():
    c = db.connect(URL)
    for kind, name in (("view", "tracked_skus"), ("view", "group_members"), ("view", "daily_prices"),
                       ("view", "prices_own"), ("table", "price_snapshots"), ("table", "scrape_runs"),
                       ("table", "spec_groups"), ("table", "product_specs"), ("table", "products"),
                       ("table", "brands")):
        c.execute(f"drop {kind} if exists {name} cascade")
    c.commit()
    yield c
    c.close()


def _all(conn, sql, *params):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def test_init_sync_views_and_pull(store, conn, tmp_path):
    db.init(conn)
    db.init(conn)                                           # the schema file is idempotent
    done = db.sync(store, conn=conn)
    assert done["products"] == 4 and done["specs"] == 4 and done["snapshots"] == 5 and done["runs"] == 1
    assert done["groups"] == len(store.spec_groups())

    # a second sync changes nothing: prices are append-only and keyed by (sku, day, source)
    again = db.sync(store, conn=conn)
    assert again["snapshots"] == 0
    assert _all(conn, "select count(*) from price_snapshots")[0][0] == 5
    assert _all(conn, "select count(*) from products")[0][0] == 4

    # the tracked set computed in SQL is the one the daily job computes locally
    tracked = sorted(r[0] for r in _all(conn, "select sku from tracked_skus"))
    assert tracked == store.tracked_skus() == ["6000001", "6000002"]

    # group membership at the exact level: Lenovo + HP, not the 8 GB Dell, not the Marketplace offer
    key = store.product_specs()["6000001"]["group_key"]
    members = sorted(r[0] for r in _all(conn, "select sku from group_members where group_key = %s", key))
    assert members == ["6000001", "6000002"]
    label, brands, tracked_flag = _all(conn, "select label, brands, tracked from spec_groups where group_key = %s", key)[0]
    assert brands == ["HP", "Lenovo"] and tracked_flag and "226V" in label

    # merged series: first-hand beats Honey, a flagged row is never served
    daily = {(r[0], str(r[1])): (float(r[2]), r[3]) for r in
             _all(conn, "select sku, snapshot_date, price, source from daily_prices")}
    assert daily[("6000001", "2026-11-19")] == (649.99, "bestbuy_graphql")
    assert daily[("6000001", "2026-11-18")] == (700.00, "honey")
    assert daily[("6000002", "2026-11-19")] == (999.99, "honey")
    own = _all(conn, "select sku, price from prices_own")
    assert [(r[0], float(r[1])) for r in own] == [("6000001", 649.99)]

    # raw spec table and arrays survive the round trip
    raw, cats = _all(conn, "select raw_specs, categories from products where sku = '6000001'")[0]
    assert raw["Processor"]["Processor Model Number"] == "226V" and cats == ["Windows Laptops"]

    # regrouping rewrites the generated tables; a pin makes the laptop tracked on its own
    store.upsert_products([{"sku": "6000003"}], pinned=True)
    collect.rebuild_groups(store, "Lenovo")
    db.sync(store, conn=conn)
    assert sorted(r[0] for r in _all(conn, "select sku from tracked_skus")) == ["6000001", "6000002", "6000003"]
    assert _all(conn, "select count(*) from spec_groups")[0][0] == len(store.spec_groups())

    # a new machine can rebuild its local store from the database
    fresh = LocalStore(tmp_path / "fresh")
    pulled = db.pull(fresh, conn=conn)
    assert pulled["products"] == 4 and pulled["snapshots"] == 5 and pulled["runs"] == 1
    assert fresh.products()["6000003"]["pinned"] is True
    assert fresh.products()["6000001"]["raw_specs"] == store.products()["6000001"]["raw_specs"]
    assert {(r["sku"], r["snapshot_date"], r["source"], r["price"], r["flagged"]) for r in fresh.snapshots()} == \
           {(r["sku"], r["snapshot_date"], r["source"], r["price"], r["flagged"]) for r in store.snapshots()}
    collect.rebuild_groups(fresh, "Lenovo")
    assert fresh.tracked_skus() == ["6000001", "6000002", "6000003"]
