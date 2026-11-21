"""Grouping: the four match levels, tracking, featured groups."""
from conftest import make_product

from tracker import grouping
from tracker.normalise import LEVEL_KEY, LEVELS

WIN = "Windows 11 Home"


def lenovo_yoga(**kw):
    """The brief's Lenovo: the title does not name the CPU model, the spec table does."""
    base = dict(cpu_brand="Intel", series="Intel Core Ultra 5 Series 2", number="226V", ram="16 gigabytes",
                storage="512 gigabytes", storage_type="SSD", graphics="Intel Arc", graphics_type="Integrated",
                os=WIN, touch="true", two_in_one="true", size="16 inches", resolution="1920 x 1200 (WUXGA)")
    base.update(kw)
    return make_product('Lenovo - Yoga 7i 2-in-1 - Copilot+ PC - 16" 2K Touchscreen Laptop - Intel Core Ultra 5 '
                        'Processor 2024 - 16GB Memory - 512GB SSD - Luna Grey', "Lenovo", **base)


def hp_flip(**kw):
    base = dict(cpu_brand="Intel", series="Intel Core Ultra 5 Series 2", number="226V", ram="16 gigabytes",
                storage="512 gigabytes", storage_type="SSD", graphics="Intel Arc 130V", graphics_type="Integrated",
                os=WIN, touch="true", two_in_one="true", size="14 inches", resolution="1920 x 1200 (WUXGA)")
    base.update(kw)
    return make_product('HP - OmniBook X Flip 2-in-1 - 14" 2K Touchscreen Laptop - Intel Core Ultra 5 226V (2024) '
                        '- 16GB Memory - 512GB SSD - Copilot+ - Atmospheric Blue', "HP", **base)


def dell_plus(**kw):
    """Shaped like Dell's real rows: 1 TB as 1000 GB elsewhere, the iGPU labelled 'Dedicated', no 2-in-1 flag."""
    base = dict(cpu_brand="Intel", series="Intel Core Ultra 5 Series 2", number="226V", ram="16 gigabytes",
                storage="512 gigabytes", storage_type="SSD", graphics="Intel Arc", graphics_type="Dedicated",
                os="Windows 11 Home", touch="true", size="16 inches", resolution="1920 x 1200 (Full HD+)")
    base.update(kw)
    return make_product('Dell - Plus 16" 2K 2-in-1 Touchscreen Laptop - Intel Core Ultra 5 Series 2 2024 - 16GB '
                        'Memory - 512GB Storage - Copilot+ PC - Ice Blue', "Dell", **base)


def build(*products, **kw):
    by_sku = {p["sku"]: p for p in products}
    specs, groups = grouping.build(by_sku, kw.pop("anchor", "Lenovo"), **kw)
    return by_sku, {s.sku: s for s in specs}, groups


def group_of(groups, spec, level="exact"):
    key = getattr(spec, LEVEL_KEY[level])
    return next(g for g in groups if g["group_key"] == key)


def test_the_briefs_example_is_one_tracked_group():
    lenovo, hp, dell = lenovo_yoga(), hp_flip(), dell_plus()
    _, specs, groups = build(lenovo, hp, dell)
    keys = {specs[p["sku"]].group_key for p in (lenovo, hp, dell)}
    assert len(keys) == 1 and None not in keys           # 14" and 16", three iGPU spellings: still one group
    g = group_of(groups, specs[lenovo["sku"]])
    assert g["brands"] == ["Dell", "HP", "Lenovo"]
    assert (g["has_anchor"], g["competitor_count"], g["tracked"]) == (True, 2, True)
    assert g["label"] == "Core Ultra 5 226V · 16 GB · 512 GB SSD · Convertible touch · Windows"


def test_near_misses_stay_out_of_the_exact_group():
    lenovo = lenovo_yoga()
    other_cpu = hp_flip(number="228V")
    other_cpu["title"] = other_cpu["title"].replace("226V", "228V")
    others = {
        "other cpu": other_cpu,
        "less memory": hp_flip(ram="8 gigabytes"),
        "less storage": hp_flip(storage="256 gigabytes"),
        "clamshell": make_product('HP - OmniBook 5 16" 2K Touchscreen Laptop - Intel Core Ultra 5 226V - 16GB Memory '
                                  '- 512GB SSD', "HP", cpu_brand="Intel", number="226V", ram="16 gigabytes",
                                  storage="512 gigabytes", storage_type="SSD", graphics="Intel Arc",
                                  graphics_type="Integrated", os=WIN, touch="true", two_in_one="false"),
        "chromeos": hp_flip(os="Chrome OS"),
        "discrete gpu": hp_flip(graphics="NVIDIA GeForce RTX 5050", graphics_type="Dedicated"),
    }
    _, specs, _ = build(lenovo, *others.values())
    for why, p in others.items():
        assert specs[p["sku"]].group_key != specs[lenovo["sku"]].group_key, why
        assert specs[p["sku"]].group_key is not None, why


def test_levels_widen_in_order():
    touch = lenovo_yoga(number="255U", series="Intel Core Ultra 7 Series 2")
    non_touch_ufs = hp_flip(number="255U", series="Intel Core Ultra 7 Series 2", touch="false", storage_type="UFS")
    non_touch_ufs["title"] = 'HP - OmniBook X Flip 2-in-1 - 14" 2K Laptop - Intel Core Ultra 7 255U - 16GB Memory - 512GB UFS'
    same_class = dell_plus(number="256V", series="Intel Core Ultra 7 Series 2")
    same_class["title"] = same_class["title"].replace("Ultra 5", "Ultra 7")
    other_tier = hp_flip(number="226V")
    _, specs, groups = build(touch, non_touch_ufs, same_class, other_tier)
    a, b, c, d = (specs[p["sku"]] for p in (touch, non_touch_ufs, same_class, other_tier))

    assert a.group_key != b.group_key and a.relaxed_key == b.relaxed_key     # relaxed ignores touch + storage type
    assert a.relaxed_key != c.relaxed_key and a.class_key == c.class_key     # class ignores the model number
    assert a.class_key != d.class_key                                        # ...but not the tier
    assert a.cpu_class == c.cpu_class == "intel-core-ultra-7-s2" and d.cpu_class == "intel-core-ultra-5-s2"
    g = group_of(groups, a, "class")
    assert g["member_count"] == 3 and g["competitor_count"] == 2
    assert g["label"] == "Core Ultra 7 Series 2 · 16 GB · 512 GB · Convertible · Windows"
    # every product's group at one level sits inside its group at the next wider level
    for s in specs.values():
        strict, exact, relaxed, cls = (set(group_of(groups, s, lvl)["skus"]) for lvl in LEVELS)
        assert strict <= exact <= relaxed <= cls


def test_strict_level_splits_an_exact_group_by_screen_size_bucket():
    lenovo16, hp14, dell16 = lenovo_yoga(), hp_flip(), dell_plus()            # 16", 14", 16"
    hp156 = hp_flip(size="15.6 inches")
    hp156["title"] = hp156["title"].replace('14"', '15.6"')
    _, specs, groups = build(lenovo16, hp14, dell16, hp156)
    a, b, c, d = (specs[p["sku"]] for p in (lenovo16, hp14, dell16, hp156))

    assert a.group_key == b.group_key == c.group_key == d.group_key           # one exact group: it ignores the screen
    assert (a.screen_bucket, b.screen_bucket, d.screen_bucket) == ("15–16", "14", "15–16")
    assert a.strict_key == c.strict_key == d.strict_key != b.strict_key       # 15.6" and 16" share a bucket, 14" does not
    big, small = group_of(groups, a, "strict"), group_of(groups, b, "strict")
    assert big["brands"] == ["Dell", "HP", "Lenovo"] and big["member_count"] == 3 and big["tracked"]
    assert big["label"] == 'Core Ultra 5 226V · 16 GB · 512 GB SSD · Convertible touch · 15–16" · Windows'
    assert small["brands"] == ["HP"] and not small["tracked"]                 # no Lenovo of that size
    assert (big["screen_bucket"], group_of(groups, a)["screen_bucket"]) == ("15–16", None)
    assert not any(g["featured_rank"] for g in groups if g["level"] == "strict")


def test_unknown_screen_size_leaves_only_the_strict_level():
    no_size = make_product("HP - OmniBook X Flip 2-in-1 Touchscreen Laptop - Intel Core Ultra 5 226V - 16GB Memory "
                           "- 512GB SSD", "HP", cpu_brand="Intel", number="226V", ram="16 gigabytes",
                           storage="512 gigabytes", storage_type="SSD", graphics="Intel Arc",
                           graphics_type="Integrated", os=WIN, touch="true", two_in_one="true")
    _, specs, groups = build(lenovo_yoga(), no_size)
    s = specs[no_size["sku"]]
    assert s.screen_size_in is None and s.strict_key is None
    assert s.group_key and not s.needs_review                                 # still grouped from exact upwards
    assert all(no_size["sku"] not in g["skus"] for g in groups if g["level"] == "strict")


def test_sub_brands_count_as_their_maker():
    lenovo = lenovo_yoga()
    omen = hp_flip()
    omen["brand"] = "HP OMEN"
    hp = hp_flip()
    _, specs, groups = build(lenovo, omen, hp)
    g = group_of(groups, specs[lenovo["sku"]])
    assert g["brands"] == ["HP", "Lenovo"] and g["competitor_count"] == 1 and g["member_count"] == 3


def test_only_new_first_party_offers_count():
    lenovo = lenovo_yoga()
    marketplace = hp_flip(seller_class="3P")
    open_box = dell_plus(condition="open-box")
    delisted = hp_flip()
    delisted["active"] = False
    by_sku, specs, groups = build(lenovo, marketplace, open_box, delisted)
    g = group_of(groups, specs[lenovo["sku"]])
    assert g["brands"] == ["Lenovo"] and not g["tracked"]
    assert specs[marketplace["sku"]].group_key == specs[lenovo["sku"]].group_key    # normalised, just not counted
    assert grouping.tracked_skus(by_sku, list(specs.values()), groups) == []
    assert [x["group_key"] for x in grouping.unmatched_anchor_groups(groups)] == [g["group_key"]]


def test_unparsed_products_are_never_grouped():
    lenovo = lenovo_yoga()
    no_cpu = make_product('HP - Omnibook 5 - 14" 2K OLED Touchscreen Laptop - Intel Core 5 Series 3 (2026) - 8GB Memory',
                          "HP", cpu_brand="Intel", series="Intel Core 5 Series 3", number="Series 3",
                          ram="8 gigabytes", storage="256 gigabytes", storage_type="SSD",
                          graphics="Intel Graphics", graphics_type="Integrated", os=WIN, touch="true")
    _, specs, groups = build(lenovo, no_cpu)
    s = specs[no_cpu["sku"]]
    assert s.needs_review and s.group_key is None and s.relaxed_key is None and s.class_key is None
    assert all(no_cpu["sku"] not in g["skus"] for g in groups)


def test_class_and_name_follow_the_majority_listing():
    full = [make_product(f'{brand} - 16" Laptop - AMD Ryzen AI 7 445 (2026) - 16GB Memory - 512GB SSD', brand,
                         cpu_brand="AMD", series="AMD Ryzen AI 400 Series", number="445", ram="16 gigabytes",
                         storage="512 gigabytes", storage_type="SSD", graphics="AMD Radeon 840M",
                         graphics_type="Integrated", os=WIN, touch="false") for brand in ("Lenovo", "HP")]
    # Acer's real row: the number field says "AMD Ryzen 7 445" (no "AI") and the series is the wrong family.
    odd = make_product('Acer - Swift Go 16 - 16" Laptop - 16GB Memory - 512GB SSD', "Acer", cpu_brand="AMD",
                       series="AMD Ryzen 7 4000 Series", number="AMD Ryzen 7 445", ram="16 gigabytes",
                       storage="512 gigabytes", storage_type="SSD", graphics="AMD Radeon 840M",
                       graphics_type="Integrated", os=WIN, touch="false")
    pro = make_product('Lenovo - ThinkPad T14 14" Laptop - AMD Ryzen AI 7 PRO 445 with 16GB Memory - 512GB SSD',
                       "Lenovo", cpu_brand="AMD", series="AMD Ryzen AI PRO 400 Series", number="445",
                       ram="16 gigabytes", storage="512 gigabytes", storage_type="SSD",
                       graphics="AMD Radeon 840M", graphics_type="Integrated", os=WIN, touch="false")
    _, specs, groups = build(*full, odd, pro)
    assert {s.cpu_model for s in specs.values()} == {"amd-445"}
    assert {s.cpu_class for s in specs.values()} == {"amd-ryzen-ai-7-400"}
    assert specs[odd["sku"]].cpu_name == "AMD Ryzen AI 7 445"
    assert specs[pro["sku"]].cpu_name == "AMD Ryzen AI 7 PRO 445"       # PRO is kept where it was stated
    assert len({s.group_key for s in specs.values()}) == 1              # ...and does not split the group


def _family(cpu_number, brands, **kw):
    """One laptop per brand with the same spec."""
    return [make_product(f'{b} - 14" Laptop - Intel Core Ultra 7 {cpu_number} - 16GB Memory - 512GB SSD', b,
                         cpu_brand="Intel", series="Intel Core Ultra 7 Series 2", number=cpu_number,
                         ram=kw.get("ram", "16 gigabytes"), storage="512 gigabytes", storage_type="SSD",
                         graphics="Intel Arc", graphics_type="Integrated", os=WIN, touch="false") for b in brands]


def test_featured_groups_are_ranked_distinct_and_as_strict_as_possible():
    wide = _family("255U", ["Lenovo", "HP"]) + _family("256V", ["Dell", "Acer"])     # 3 competitors only at class level
    tight = _family("258V", ["Lenovo", "HP", "Dell"], ram="32 gigabytes")            # 2 competitors already at exact level
    lonely = _family("265U", ["Lenovo", "HP"], ram="8 gigabytes")                    # 1 competitor: not featured
    by_sku, specs, groups = build(*wide, *tight, *lonely)
    featured = sorted((g for g in groups if g["featured_rank"]), key=lambda g: g["featured_rank"])

    assert [g["featured_rank"] for g in featured] == [1, 2]
    assert (featured[0]["level"], featured[0]["competitor_count"]) == ("class", 3)
    assert (featured[1]["level"], featured[1]["competitor_count"]) == ("exact", 2)   # not widened without a gain
    shared = set(featured[0]["skus"]) & set(featured[1]["skus"])
    assert not shared
    # every laptop of a tracked group is priced daily, featured or not
    assert grouping.tracked_skus(by_sku, list(specs.values()), groups) == sorted(by_sku)


def test_a_pinned_laptops_group_is_featured_first():
    big = _family("255U", ["Lenovo", "HP", "Dell", "Acer"])
    pinned_pair = _family("265U", ["Lenovo", "HP"], ram="8 gigabytes")
    pinned_pair[0]["pinned"] = True
    _, specs, groups = build(*big, *pinned_pair)
    first = next(g for g in groups if g["featured_rank"] == 1)
    assert first["level"] == "exact" and set(first["skus"]) == {p["sku"] for p in pinned_pair}
    second = next(g for g in groups if g["featured_rank"] == 2)
    assert second["competitor_count"] == 3


def test_anchor_brand_is_configurable():
    lenovo, hp, dell = lenovo_yoga(), hp_flip(), dell_plus()
    _, specs, groups = build(lenovo, hp, dell, anchor="HP")
    g = group_of(groups, specs[hp["sku"]])
    assert g["has_anchor"] and g["competitor_count"] == 2
    _, specs, groups = build(lenovo, dell, anchor="HP")
    assert not group_of(groups, specs[lenovo["sku"]])["tracked"]
