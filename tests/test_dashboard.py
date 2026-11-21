"""The dashboard's data layer: which price is shown for a day, and the figures derived from it."""
import pandas as pd
import pytest

pytest.importorskip("pandas")

from tracker import dashboard as dd  # noqa: E402
from tracker.store import LocalStore  # noqa: E402


def _prices(rows):
    frame = pd.DataFrame(rows, columns=["sku", "snapshot_date", "price", "list_price", "source", "flagged"])
    return dd._prices_frame(frame)


PRICES = _prices([
    ("A", "2026-11-16", 900.00, None, "honey", False),
    ("A", "2026-11-17", 900.00, None, "honey_carried", False),
    ("A", "2026-11-18", 850.00, None, "honey", False),
    ("A", "2026-11-19", 860.00, None, "honey_carried", False),          # loses to the first-hand row below
    ("A", "2026-11-19", 849.99, 999.99, "bestbuy_graphql", False),
    ("A", "2026-11-20", 1.00, 999.99, "bestbuy_graphql", True),          # flagged: never shown
    ("B", "2026-11-16", 700.00, None, "shopsavvy", False),
    ("B", "2026-11-16", 705.00, None, "honey_carried", False),           # Honey outranks ShopSavvy
    ("B", "2026-11-19", 650.00, 800.00, "bestbuy_graphql", False),       # 17th and 18th: nobody observed
    ("C", "2026-11-19", 500.00, None, "bestbuy_graphql", False),         # not in the selection
])


def test_merged_series_takes_the_most_trusted_source_per_day():
    rows = dd.select_prices(PRICES, ["A", "B"], "merged")
    got = {(r.sku, f"{r.date:%m-%d}"): (r.price, r.source) for r in rows.itertuples()}
    assert got == {
        ("A", "11-16"): (900.00, "honey"), ("A", "11-17"): (900.00, "honey_carried"),
        ("A", "11-18"): (850.00, "honey"), ("A", "11-19"): (849.99, "bestbuy_graphql"),
        ("B", "11-16"): (705.00, "honey_carried"), ("B", "11-19"): (650.00, "bestbuy_graphql"),
    }


def test_own_mode_shows_first_hand_prices_only():
    rows = dd.select_prices(PRICES, ["A", "B"], "own")
    assert sorted((r.sku, r.price) for r in rows.itertuples()) == [("A", 849.99), ("B", 650.00)]
    assert set(rows["source"]) == {dd.OWN}


def test_missing_days_stay_gaps():
    price, source = dd.to_daily(dd.select_prices(PRICES, ["A", "B"], "merged"))
    assert [f"{d:%m-%d}" for d in price.index] == ["11-16", "11-17", "11-18", "11-19"]
    assert pd.isna(price.at[pd.Timestamp("2026-11-17"), "B"]) and pd.isna(price.at[pd.Timestamp("2026-11-18"), "B"])
    assert source.at[pd.Timestamp("2026-11-19"), "A"] == dd.OWN
    empty_price, empty_source = dd.to_daily(dd.select_prices(PRICES, ["nope"], "merged"))
    assert empty_price.empty and empty_source.empty


def test_lowest_per_brand_names_the_laptop_and_its_source():
    price, source = dd.to_daily(dd.select_prices(PRICES, ["A", "B"], "merged"))
    lows = dd.lowest_per_brand(price, source, {"A": "Lenovo", "B": "Lenovo"})
    lenovo = lows["Lenovo"]
    assert list(lenovo["price"]) == [705.00, 900.00, 850.00, 650.00]
    assert list(lenovo["sku"]) == ["B", "A", "A", "B"]
    assert list(lenovo["source"]) == ["honey_carried", "honey_carried", "honey", dd.OWN]

    split = dd.lowest_per_brand(price, source, {"A": "Lenovo", "B": "HP"})
    assert pd.isna(split["HP"].at[pd.Timestamp("2026-11-17"), "price"])            # a gap stays a gap
    assert pd.isna(split["HP"].at[pd.Timestamp("2026-11-17"), "sku"])


def test_days_at_current_price_stops_at_a_change_or_a_gap():
    days = pd.date_range("2026-11-10", periods=6, freq="D")
    assert dd.days_at_current_price(pd.Series([5, 4, 4, 4, 4, 4.0], index=days)) == 5
    assert dd.days_at_current_price(pd.Series([4, 4, None, 4, 4, 4.0], index=days)) == 3
    assert dd.days_at_current_price(pd.Series([4, 4, 4, 5, None, None], index=days)) == 1
    assert dd.days_at_current_price(pd.Series([None] * 6, index=days, dtype=float)) is None


def test_latest_prices_ignore_laptops_that_stopped_being_priced():
    days = pd.date_range("2026-11-10", periods=10, freq="D")
    price = pd.DataFrame({"live": [10.0] * 10, "gone": [20.0] * 4 + [None] * 6,
                          "late": [None] * 8 + [30.0, None]}, index=days)
    latest = dd.latest_prices(price)
    assert latest["live"] == 10.0 and latest["late"] == 30.0 and pd.isna(latest["gone"])


def test_summary_per_laptop():
    rows = dd.select_prices(PRICES, ["A", "B"], "merged")
    price, _ = dd.to_daily(rows)
    table = dd.summarise(price, price, rows).set_index("sku")
    a = table.loc["A"]
    assert (a["price"], a["list_price"], a["low"], a["high"], a["days_at_price"]) == (849.99, 999.99, 849.99, 900.00, 1)
    assert a["discount"] == pytest.approx(0.15, abs=1e-3)
    assert f"{a['low_date']:%m-%d}" == "11-19"
    assert a["trend"] == [900.00, 900.00, 850.00, 849.99]
    b = table.loc["B"]
    assert (b["price"], b["low"], b["high"], b["days_at_price"]) == (650.00, 650.00, 705.00, 1)
    assert b["trend"] == [705.00, 705.00, 705.00, 650.00]        # the sparkline carries a price across a gap


def test_source_coverage_and_first_own_day():
    table = dd.source_coverage(PRICES).set_index("source")
    assert (table.at[dd.OWN, "rows"], table.at[dd.OWN, "laptops"]) == (3, 3)          # the flagged row is not counted
    assert (table.at["honey", "rows"], table.at["honey_carried", "rows"]) == (2, 3)
    assert list(table["method"]) == ["Own scraper (Best Buy)", "Honey", "Honey", "ShopSavvy"]
    assert f"{dd.first_own_day(PRICES):%Y-%m-%d}" == "2026-11-19"
    assert dd.own_day_count(PRICES) == 1


def _explorer_data() -> dd.Data:
    def spec(sku, brand, cpu, cls, mem, sto, kind, form, touch, os_, gpu, size, review=False):
        return {"sku": sku, "brand": brand, "cpu_model": cpu.lower(), "cpu_name": cpu, "cpu_class": cls,
                "memory_gb": mem, "storage_gb": sto, "storage_type": kind, "form_factor": form, "touchscreen": touch,
                "os": os_, "gpu": gpu, "screen_size_in": size, "resolution": "1920x1200", "needs_review": review}
    specs = pd.DataFrame([
        spec("A", "Lenovo", "Intel Core Ultra 7 255U", "intel-core-ultra-7-s2", 16, 512, "ssd", "clamshell", False, "windows", "integrated", 14.0),
        spec("B", "HP", "Intel Core Ultra 7 256V", "intel-core-ultra-7-s2", 32, 1024, "ssd", "convertible", True, "windows", "integrated", 16.0),
        spec("C", "Acer", "Intel Processor N150", "intel-n150", 4, 64, "emmc", "clamshell", False, "chromeos", "integrated", 11.6),
        spec("D", "ASUS", "AMD Ryzen 9 270", "amd-ryzen-9-200", 32, 1024, "ssd", "clamshell", False, "windows", "nvidia-rtx-5070-ti", None),
        spec("E", "Dell", "Intel Core Ultra 7 255U", "intel-core-ultra-7-s2", 16, 512, "ssd", "clamshell", False, "windows", "integrated", 14.0, review=True),
        spec("F", "Dell", "Intel Core Ultra 7 255U", "intel-core-ultra-7-s2", 16, 512, "ssd", "clamshell", False, "windows", "integrated", 14.0),
    ]).set_index("sku")
    products = pd.DataFrame([
        {"sku": s, "title": s, "brand": b, "url": "u", "seller_class": "3P" if s == "F" else "1P",
         "condition": "new", "pinned": False, "active": True}
        for s, b in zip("ABCDEF", ["Lenovo", "HP", "Acer", "ASUS", "Dell", "Dell"])]).set_index("sku")
    prices = _prices([("A", "2026-11-18", 900.0, None, "honey", False), ("A", "2026-11-19", 850.0, None, "bestbuy_graphql", False),
                      ("B", "2026-11-19", 1400.0, None, "bestbuy_graphql", False),
                      ("C", "2026-11-19", 199.0, None, "bestbuy_graphql", False)])
    return dd.Data("test", products, specs, pd.DataFrame(), prices, pd.DataFrame())


def test_spec_table_lists_only_comparable_laptops():
    table = dd.spec_table(_explorer_data())
    assert list(table.index) == ["A", "B", "C", "D"]              # E needs review, F is a Marketplace offer
    assert table.at["A", "cpu_family"] == "Core Ultra 7 Series 2" and table.at["C", "cpu_family"] == "Intel Processor N150"
    assert table.at["D", "graphics"] == "RTX 5070 Ti" and table.at["A", "graphics"] == "Integrated"
    assert table.at["A", "price"] == 850.0 and pd.isna(table.at["D", "price"])


def test_spec_filters_combine():
    table = dd.spec_table(_explorer_data())

    def match(**criteria):
        return list(dd.filter_specs(table, **criteria).index)

    assert match() == ["A", "B", "C", "D"]
    assert match(brands=[], cpus=None) == ["A", "B", "C", "D"]                     # empty means no constraint
    assert match(cpu_families=["Core Ultra 7 Series 2"]) == ["A", "B"]
    assert match(cpus=["Intel Core Ultra 7 256V", "Intel Processor N150"]) == ["B", "C"]
    assert match(memory=(16, 32)) == ["A", "B", "D"] and match(memory=(32, 32)) == ["B", "D"]
    assert match(storage=(512, 1024), storage_types=["ssd"], os=["windows"]) == ["A", "B", "D"]
    assert match(form_factors=["convertible"]) == ["B"] and match(touch=False) == ["A", "C", "D"]
    assert match(graphics=["Integrated"]) == ["A", "B", "C"] and match(graphics=["RTX 5070 Ti"]) == ["D"]
    assert match(screen=(13.0, 15.0)) == ["A"]                                     # no screen size on file: excluded
    assert match(price=(100, 900)) == ["A", "C"]                                   # no price on file: excluded
    assert match(brands=["Lenovo", "HP"], cpu_families=["Core Ultra 7 Series 2"], memory=(32, 64), touch=True) == ["B"]
    assert match(brands=["Lenovo"], os=["chromeos"]) == []


def test_table_shows_the_features_a_match_level_ignores():
    assert dd.differing_features("strict") == []
    assert dd.differing_features("exact") == ["screen_size"]
    assert dd.differing_features("relaxed") == ["storage_type", "touchscreen", "screen_size"]     # touchscreen: not in exact
    assert dd.differing_features("class") == ["cpu", "storage_type", "touchscreen", "screen_size"]
    assert dd.differing_features(None) == ["cpu", "memory", "storage", "storage_type", "form", "touchscreen",
                                           "screen_size"]                                        # not a group: everything


def test_capacity_labels():
    assert dd.capacity_label(512) == "512 GB" and dd.capacity_label(1024) == "1 TB" and dd.capacity_label(2048, "ssd") == "2 TB SSD"
    assert dd.capacity_label(64, "emmc") == "64 GB eMMC" and dd.capacity_label(None) == ""


def test_loads_the_local_store_and_names_laptops(tmp_path):
    store = LocalStore(tmp_path)
    store.upsert_products([
        {"sku": "6000001", "title": 'Lenovo - Yoga 7i 2-in-1 16" Laptop', "brand": "Lenovo", "seller_class": "1P",
         "condition": "new", "url": "u1"},
        {"sku": "6000002", "title": 'HP - OmniBook X Flip 14" Laptop - Blue', "brand": "HP OMEN", "seller_class": "1P",
         "condition": "new", "url": "u2"},
        {"sku": "6000003", "title": 'HP - OmniBook X Flip 14" Laptop - Silver', "brand": "HP", "seller_class": "1P",
         "condition": "new", "url": "u3"},
    ])
    store.save_product_specs([
        {"sku": "6000001", "brand": "Lenovo", "model_family": "Yoga 7i 2-in-1", "screen_size_in": 16.0, "color": "Grey"},
        {"sku": "6000002", "brand": None, "model_family": "OmniBook X Flip", "screen_size_in": 14.0, "color": "Blue"},
        {"sku": "6000003", "brand": "HP", "model_family": "OmniBook X Flip", "screen_size_in": 14.0, "color": "Silver"},
    ])
    store.save_spec_groups([{"group_key": "k", "level": "exact", "label": "x", "brands": ["HP", "Lenovo"],
                             "skus": ["6000001", "6000002", "6000003"], "tracked": True, "featured_rank": 1}])
    store.insert_snapshots([{"sku": "6000001", "snapshot_date": "2026-11-19", "price": 649.99, "list_price": 999.99,
                             "source": "bestbuy_graphql"}])
    data = dd.load_local(store)
    assert data.origin == "local store" and len(data.products) == 3 and len(data.prices) == 1
    assert data.groups.iloc[0]["skus"] == ["6000001", "6000002", "6000003"]

    skus = ["6000001", "6000002", "6000003"]
    assert dd.brand_of(skus, data) == {"6000001": "Lenovo", "6000002": "HP", "6000003": "HP"}   # sub-brand folded in
    assert dd.short_names(skus, data) == {
        "6000001": 'Yoga 7i 2-in-1 16"',
        "6000002": 'OmniBook X Flip 14" · Blue',            # same model twice: told apart by colour
        "6000003": 'OmniBook X Flip 14" · Silver',
    }
    assert dd.value(data.specs, "6000002", "brand") is None and dd.value(data.specs, "nope", "brand") is None
