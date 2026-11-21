"""The local store: merge rules, idempotent price rows, delisting."""
from tracker.store import LocalStore


def _p(sku, **kw):
    base = {"sku": sku, "title": f"Laptop {sku}", "brand": "Lenovo", "seller_class": "1P",
            "condition": "new", "url": f"https://www.bestbuy.com/site/{sku}.p?skuId={sku}",
            "raw_specs": {"General": {"Brand": "Lenovo"}}}
    base.update(kw)
    return base


def test_upsert_keeps_first_seen_and_spec_table(tmp_path):
    store = LocalStore(tmp_path)
    assert store.upsert_products([_p("6000001")]) == (1, 0)
    first = store.products()["6000001"]

    # A later listing-only refresh carries no spec table: it must not wipe the stored one.
    assert store.upsert_products([_p("6000001", title="Renamed", raw_specs=None)]) == (0, 1)
    again = store.products()["6000001"]
    assert again["first_seen"] == first["first_seen"]
    assert again["title"] == "Renamed"
    assert again["raw_specs"] == {"General": {"Brand": "Lenovo"}}


def test_pin_is_sticky(tmp_path):
    store = LocalStore(tmp_path)
    store.upsert_products([_p("6000001")], pinned=True)
    store.upsert_products([_p("6000001")])            # a brand crawl sees it again
    assert store.products()["6000001"]["pinned"] is True


def test_snapshots_are_unique_per_sku_day_source(tmp_path):
    store = LocalStore(tmp_path)
    row = {"sku": "6000001", "snapshot_date": "2026-11-19", "price": 649.99, "source": "bestbuy_graphql"}
    assert store.insert_snapshots([row]) == 1
    assert store.insert_snapshots([dict(row, price=1.0)]) == 0          # same key: history is never rewritten
    assert store.insert_snapshots([dict(row, source="honey")]) == 1     # another source keeps its own row
    assert store.insert_snapshots([dict(row, snapshot_date="2026-11-20")]) == 1
    assert store.insert_snapshots([dict(row, snapshot_date="2026-11-21", price=None)]) == 0
    prices = {(r["snapshot_date"], r["source"]): r["price"] for r in store.snapshots()}
    assert prices[("2026-11-19", "bestbuy_graphql")] == 649.99
    assert len(prices) == 3


def test_three_consecutive_misses_deactivate(tmp_path):
    store = LocalStore(tmp_path)
    store.upsert_products([_p("6000001"), _p("6000002")])
    assert store.record_misses([], ["6000001"]) == []
    assert store.record_misses([], ["6000001"]) == []
    assert store.record_misses(["6000001"], []) == []                   # seen again: counter resets
    assert store.products()["6000001"]["miss_count"] == 0
    for _ in range(2):
        assert store.record_misses([], ["6000001"]) == []
    assert store.record_misses([], ["6000001"]) == ["6000001"]
    products = store.products()
    assert products["6000001"]["active"] is False and products["6000002"]["active"] is True


def test_tracked_skus_follow_groups_and_pins(tmp_path):
    store = LocalStore(tmp_path)
    store.upsert_products([_p("6000001"), _p("6000002"), _p("6000003"),
                           _p("16000004", seller_class="3P"), _p("6000005")])
    store.upsert_products([_p("6000005")], pinned=True)
    store.save_spec_groups([{"group_key": "aaa", "tracked": True}, {"group_key": "ccc", "tracked": True},
                            {"group_key": "bbb", "tracked": False}])
    store.save_product_specs([
        {"sku": "6000001", "group_key": "aaa", "relaxed_key": "r1", "class_key": "c1"},
        {"sku": "6000002", "group_key": "bbb", "relaxed_key": "r2", "class_key": "ccc"},   # tracked at class level only
        {"sku": "6000003", "group_key": "bbb", "relaxed_key": "r2", "class_key": "c9"},
        {"sku": "16000004", "group_key": "aaa", "relaxed_key": "r1", "class_key": "c1"},   # Marketplace: never tracked
        {"sku": "6000005", "group_key": None, "relaxed_key": None, "class_key": None},     # pinned: always tracked
    ])
    assert store.tracked_skus() == ["6000001", "6000002", "6000005"]
