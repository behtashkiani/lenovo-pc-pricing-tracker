"""Source modules, without touching the network."""
import datetime as dt

from tracker import collect
from tracker.sources import bestbuy_graphql as bb
from tracker.sources import honey, shopsavvy


# ------------------------------------------------------------------ Best Buy
def test_product_from_gateway_payload():
    p = bb.Product.from_gql({
        "skuId": "6615769", "brand": "Lenovo", "bsin": "JJGH3Q8P2K", "upc": "199273537594",
        "openBoxCondition": None, "name": {"short": "Lenovo - Yoga 7i 2-in-1"},
        "seller": {"classification": "1P"}, "price": {"customerPrice": 649.99, "regularPrice": 999.99},
        "hierarchy": {"bbypres": [{"primary": True, "id": "x", "categoryDetail": {"name": "Windows Laptops"}}]},
        "specificationGroups": [{"name": "Processor", "specifications": [
            {"displayName": "Processor Model Number", "value": "226V"}]}],
    })
    assert (p.sku, p.price, p.list_price, p.seller_class, p.condition) == ("6615769", 649.99, 999.99, "1P", "new")
    assert p.raw_specs == {"Processor": {"Processor Model Number": "226V"}}
    assert p.categories == ["Windows Laptops"]
    assert p.url == "https://www.bestbuy.com/site/6615769.p?skuId=6615769"
    assert bb.Product.from_gql(None) is None and bb.Product.from_gql({}) is None


def test_listing_payload_has_no_spec_table():
    p = bb.Product.from_gql({"skuId": "6615769", "name": {"short": "x"}, "price": None})
    assert p.raw_specs is None and p.price is None


def test_condition_is_inferred_when_not_filtered():
    assert bb.infer_condition("Lenovo - Refurbished Excellent - Yoga Gen 4", None) == "refurbished"
    assert bb.infer_condition("Geek Squad Certified Refurbished HP 14", None) == "refurbished"
    assert bb.infer_condition("Dell - Open-Box Latitude", None) == "open-box"
    assert bb.infer_condition("HP - OmniBook X Flip", "excellent") == "open-box"
    assert bb.infer_condition("HP - OmniBook X Flip 2-in-1", None) == "new"


def test_sku_and_bsin_from_urls():
    assert bb.sku_from_url("https://www.bestbuy.com/site/lenovo-yoga/6615769.p?skuId=6615769") == "6615769"
    assert bb.sku_from_url("https://www.bestbuy.com/product/some-slug/JJGH3Q8P2K/sku/12789225") == "12789225"
    assert bb.sku_from_url("https://www.bestbuy.com/product/some-slug/JJGH3Q8P2K") is None
    assert bb.bsin_from_url("https://www.bestbuy.com/product/some-slug/JJGH3Q8P2K") == "JJGH3Q8P2K"
    assert bb.bsin_from_url("https://www.bestbuy.com/site/6615769.p?skuId=6615769") is None


def test_snapshot_rows_skip_unpriced_products():
    priced = bb.Product(sku="1", title="a", brand="b", price=10.0, list_price=12.0, scraped_at="2026-11-19T10:00:00+00:00")
    unpriced = bb.Product(sku="2", title="a", brand="b")
    rows = collect.snapshot_rows([priced, unpriced], run_id=7)
    assert [(r["sku"], r["price"], r["list_price"], r["source"], r["run_id"]) for r in rows] == \
           [("1", 10.0, 12.0, "bestbuy_graphql", 7)]


def test_outliers_are_flagged_not_dropped():
    today = dt.date.fromisoformat(collect.today_utc())
    history = [{"sku": "1", "source": "bestbuy_graphql", "price": 1000.0,
                "snapshot_date": (today - dt.timedelta(days=d)).isoformat()} for d in (1, 2, 3)]
    rows = [{"sku": "1", "price": 99.0}, {"sku": "1", "price": 899.0}, {"sku": "2", "price": 5.0}]
    assert collect.flag_outliers(rows, history) == 1
    assert rows[0].get("flagged") is True and "flagged" not in rows[1]
    assert "flagged" not in rows[2]          # no history to judge against


# ------------------------------------------------------------------ Honey
def test_honey_id_is_derived_from_the_sku():
    assert honey.honey_id("6668500") == "28_7281cd8e117642d608b14428e95f7fb3_7281cd8e117642d608b14428e95f7fb3"
    assert honey.honey_id(" 6668500 ") == honey.honey_id("6668500")


def test_honey_change_log_becomes_daily_rows():
    today = dt.date(2026, 11, 19)
    history = {"prevPoint": {"day": "2026-11-01", "lo": 62999},
               "history": [{"day": "2026-11-10", "lo": 49999, "hi": 49999},
                           {"day": "2026-11-13", "lo": 57999, "hi": 62999}]}
    checked = int(dt.datetime(2026, 11, 15, 12, tzinfo=dt.timezone.utc).timestamp() * 1000)
    rows = honey.daily_rows("6668500", history, {"updated": checked}, days=400, today=today)
    by_day = {r["snapshot_date"]: (r["price"], r["source"]) for r in rows}

    assert by_day["2026-11-01"] == (629.99, "honey")              # a recorded change point
    assert by_day["2026-11-09"] == (629.99, "honey_carried")      # held until the next change
    assert by_day["2026-11-10"] == (499.99, "honey")
    assert by_day["2026-11-13"] == (579.99, "honey")              # `lo` is the price paid
    assert by_day["2026-11-15"] == (579.99, "honey_carried")
    assert max(by_day) == "2026-11-15"                             # nothing after Honey last looked
    assert len(rows) == 15


def test_honey_without_last_check_stops_at_last_change():
    history = {"history": [{"day": "2026-11-10", "lo": 49999}, {"day": "2026-11-12", "lo": 45000}]}
    rows = honey.daily_rows("1", history, None, today=dt.date(2026, 11, 19))
    assert [r["snapshot_date"] for r in rows] == ["2026-11-10", "2026-11-11", "2026-11-12"]
    assert honey.daily_rows("1", {}, None) == []


def test_honey_respects_the_window():
    history = {"prevPoint": {"day": "2025-01-01", "lo": 10000}, "history": [{"day": "2026-11-18", "lo": 9000}]}
    rows = honey.daily_rows("1", history, None, days=5, today=dt.date(2026, 11, 19))
    assert [r["snapshot_date"] for r in rows] == ["2026-11-14", "2026-11-15", "2026-11-16", "2026-11-17", "2026-11-18"]
    assert rows[0]["source"] == "honey_carried" and rows[-1]["source"] == "honey"


# ------------------------------------------------------------------ ShopSavvy
def _offer(**kw):
    base = {"id": "o1", "retailer": "Best Buy", "condition": "new", "seller": None, "is_marketplace": False,
            "URL": "https://www.bestbuy.com/site/some-laptop/6615769.p?skuId=6615769", "history": []}
    base.update(kw)
    return base


def test_shopsavvy_windows_and_credit_estimate():
    one = shopsavvy.windows(dt.date(2025, 11, 19), dt.date(2026, 4, 23))
    assert one == [(dt.date(2025, 11, 19), dt.date(2026, 4, 23))]
    two = shopsavvy.windows(dt.date(2025, 1, 1), dt.date(2026, 4, 23))
    assert two == [(dt.date(2025, 1, 1), dt.date(2026, 1, 1)), (dt.date(2026, 1, 2), dt.date(2026, 4, 23))]
    assert all((b - a).days <= 365 for a, b in two)
    # documented example: 100 laptops over 90 days in one request = 100 x 2 + 90
    assert shopsavvy.estimate_credits(100, dt.date(2026, 1, 1), dt.date(2026, 3, 31)) == 290
    # 101 laptops need two requests, so the per-day part is charged twice
    assert shopsavvy.estimate_credits(101, dt.date(2026, 1, 1), dt.date(2026, 3, 31)) == 202 + 180


def test_shopsavvy_picks_only_the_first_party_new_offer_for_the_sku():
    marketplace = _offer(id="mp", seller="ACME Deals", is_marketplace=True)
    used = _offer(id="used", condition="used")
    amazon = _offer(id="amz", retailer="Amazon", URL="https://www.amazon.com/dp/B0XXXX")
    other_sku = _offer(id="other", URL="https://www.bestbuy.com/site/x/6613868.p?skuId=6613868")
    good = _offer(id="good")
    entry = {"offers": [marketplace, used, amazon, other_sku, good]}
    assert shopsavvy.pick_offer(entry, "6615769")[0]["id"] == "good"
    assert shopsavvy.pick_offer({"offers": [marketplace, used, amazon]}, "6615769")[0] is None
    assert shopsavvy.pick_offer({"offers": [other_sku]}, "6615769")[0] is None      # another colour/config

    # An offer URL without a SKU is accepted only when ShopSavvy itself names our SKU.
    bare = _offer(id="bare", URL="https://www.bestbuy.com/product/slug/JJGH3Q8P2K")
    assert shopsavvy.pick_offer({"offers": [bare], "identifiers": {"bestbuy": "6615769"}}, "6615769")[0]["id"] == "bare"
    assert shopsavvy.pick_offer({"offers": [bare], "identifiers": {"bestbuy": "1111111"}}, "6615769")[0] is None
    assert shopsavvy.pick_offer({"offers": [bare]}, "6615769")[0] is None


def test_shopsavvy_points_become_daily_rows():
    offer = _offer(history=[   # newest first, as the API returns them
        {"timestamp": "2026-03-05T18:00:00.000Z", "price": 899.99, "currency": "USD", "availability": "out"},
        {"timestamp": "2026-03-05T02:00:00.000Z", "price": 949.99, "currency": "USD", "availability": "in"},
        {"timestamp": "2026-03-04T10:00:00.000Z", "price": 777.00, "currency": None},        # no currency: ignored
        {"timestamp": "2026-03-02T09:30:00.000Z", "price": 999.99, "currency": "USD"},
    ])
    rows = shopsavvy.offer_rows("6615769", offer)
    assert [(r["snapshot_date"], r["price"], r["source"], r["in_stock"]) for r in rows] == [
        ("2026-03-02", 999.99, "shopsavvy", None),
        ("2026-03-03", 999.99, "shopsavvy_carried", None),
        ("2026-03-04", 999.99, "shopsavvy_carried", None),
        ("2026-03-05", 899.99, "shopsavvy", False),          # last observation of the day wins
    ]
    assert shopsavvy.offer_rows("6615769", _offer()) == []


def test_shopsavvy_backfill_maps_results_by_upc_not_position():
    class FakeClient:
        credits_used, credits_remaining = 0, None

        def history(self, ids, start, end):
            assert ids == ["111111111111", "222222222222"]
            point = {"timestamp": "2026-03-02T09:30:00.000Z", "price": 500.0, "currency": "USD"}
            return [{"match": {"identifier": "222222222222", "by": "barcode"},      # only the second one matched
                     "offers": [_offer(URL="https://www.bestbuy.com/site/x/6000002.p?skuId=6000002", history=[point])]}]

    products = [{"sku": "6000001", "upc": "111111111111"}, {"sku": "6000002", "upc": "222222222222"},
                {"sku": "6000003", "upc": None}]
    rows, skipped = shopsavvy.backfill(products, dt.date(2026, 3, 1), dt.date(2026, 3, 31), client=FakeClient())
    assert [(r["sku"], r["price"]) for r in rows] == [("6000002", 500.0)]
    assert skipped == {"6000001": "not found by UPC", "6000003": "no UPC on file"}
