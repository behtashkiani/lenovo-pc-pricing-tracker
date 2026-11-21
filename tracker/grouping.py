"""Equivalence grouper: products whose normalised specs are identical form a group.

Four match levels, each a widening of the one before:

    strict   exact, plus the same screen size bucket (≤13, 14, 15–16, 17+ inches)
    exact    identical key specs: CPU model, memory, storage size and type,
             form factor, touchscreen, OS, GPU
    relaxed  same CPU model, ignoring storage type and touchscreen
    class    same CPU class (family, tier, generation) instead of the same model

The spec groups are an output of the tracker, never typed by hand: every distinct
key among the collected laptops is one group. A group is *tracked* (its members
get a daily price) when it contains the anchor brand and at least one other brand.
The *featured* groups are the handful with the most competing brands.
"""
from __future__ import annotations

import os
from collections import Counter, defaultdict

from . import config
from .normalise import (LEVEL_FIELDS, LEVEL_KEY, LEVELS, Specs, assign_keys, canonical_brand,
                        group_label, normalise)
from .store import now_iso

# A group needs the anchor brand plus at least this many other brands to be tracked.
MIN_COMPETITORS = int(os.environ.get("MIN_COMPETITORS", "1"))
# Featured groups: how many, and how many other brands each must have.
FEATURED_COUNT = int(os.environ.get("FEATURED_COUNT", "10"))
FEATURED_MIN_COMPETITORS = int(os.environ.get("FEATURED_MIN_COMPETITORS", "2"))


def eligible(product: dict) -> bool:
    """Only New offers sold by Best Buy itself count towards a group: Marketplace
    and open-box prices are different offers, not the Best Buy price."""
    return (product.get("active", True)
            and product.get("seller_class") == "1P"
            and (product.get("condition") or "new") == "new")


def _majority(counter: Counter) -> str:
    return max(counter, key=lambda k: (counter[k], len(k), k))


def harmonise_cpus(specs: list[Specs]) -> None:
    """Give every listing of one CPU model the same class and the same display name.

    A listing can only state its own class when it names the tier ("Ryzen AI 7");
    some do not, and a few name the wrong family ("Ryzen 7 445" for a Ryzen AI 7
    445). The class and the name of a model number are therefore the ones most of
    its listings agree on. A model with no class at all keeps the model itself as
    its class. Identity (cpu_model) is never touched; keys are recomputed.
    """
    classes: dict[str, Counter] = defaultdict(Counter)
    names: dict[str, Counter] = defaultdict(Counter)
    for s in specs:
        if not s.cpu_model:
            continue
        if s.cpu_class:
            classes[s.cpu_model][s.cpu_class] += 1
        if s.cpu_name:
            names[s.cpu_model][s.cpu_name.replace(" PRO ", " ")] += 1
    for s in specs:
        if not s.cpu_model:
            continue
        s.cpu_class = _majority(classes[s.cpu_model]) if classes[s.cpu_model] else s.cpu_model
        if names[s.cpu_model]:
            base = _majority(names[s.cpu_model])
            if " PRO " in (s.cpu_name or ""):       # PRO is per listing: keep it where it was stated
                head, _, number = base.rpartition(" ")
                base = f"{head} PRO {number}"
            s.cpu_name = base
        assign_keys(s)


def _summarise(key: str, level: str, members: list[Specs], anchor: str, min_competitors: int) -> dict:
    first = members[0]
    brands = Counter(m.brand or "?" for m in members)
    competitors = sorted(b for b in brands if b.lower() != anchor.lower())
    has_anchor = any(b.lower() == anchor.lower() for b in brands)
    # The same CPU is named slightly differently per listing; label with the commonest full name.
    names = Counter(m.cpu_name for m in members if m.cpu_name)
    cpu_name = max(names, key=lambda n: (names[n], len(n), n)) if names else None
    spec = {f: getattr(first, f) for f in set(LEVEL_FIELDS["strict"]) | {"cpu_class"}}
    kept = set(LEVEL_FIELDS[level])
    return {
        "group_key": key,
        "level": level,
        "label": group_label(spec, cpu_name, level),
        "cpu": first.cpu_class if level == "class" else first.cpu_model,
        "memory_gb": first.memory_gb,
        "storage_gb": first.storage_gb,
        "storage_type": first.storage_type if "storage_type" in kept else None,
        "form_factor": first.form_factor,
        "touchscreen": first.touchscreen if "touchscreen" in kept else None,
        "screen_bucket": first.screen_bucket if "screen_bucket" in kept else None,
        "os": first.os,
        "gpu": first.gpu,
        "member_count": len(members),
        "brand_count": len(brands),
        "competitor_count": len(competitors),
        "brands": sorted(brands),
        "has_anchor": has_anchor,
        "tracked": has_anchor and len(competitors) >= min_competitors,
        "featured_rank": None,
        "skus": sorted(m.sku for m in members),
    }


def _order(g: dict) -> tuple:
    """Tracked first, then by number of competitor brands, then by size."""
    return (not g["tracked"], not g["has_anchor"], -g["competitor_count"], -g["member_count"], g["label"])


def pick_featured(groups: list[dict], specs: list[Specs], pinned: set[str],
                  count: int = FEATURED_COUNT, min_competitors: int = FEATURED_MIN_COMPETITORS) -> list[dict]:
    """Choose the headline comparison groups and number them 1..count.

    1. The exact group of every pinned laptop comes first (it was asked for by URL).
    2. Then one group per class-level family, families ordered by competitor count.
       Each family is shown at the narrowest of class, relaxed and exact that still
       contains all of its competitor brands: widening is used only where it adds a brand.
    Featured groups never share a laptop. The strict level is never featured: splitting
    a headline comparison by screen size is a choice for the reader to make.
    """
    by_key = {g["group_key"]: g for g in groups}
    by_sku = {s.sku: s for s in specs}
    chosen: list[dict] = []
    used: set[str] = set()

    def take(g: dict) -> None:
        if g["group_key"] not in {c["group_key"] for c in chosen} and not used & set(g["skus"]):
            chosen.append(g)
            used.update(g["skus"])

    for sku in sorted(pinned):
        s = by_sku.get(sku)
        g = by_key.get(s.group_key) if s and s.group_key else None
        if g and g["has_anchor"]:
            take(g)

    families = sorted((g for g in groups if g["level"] == "class" and g["has_anchor"]
                       and g["competitor_count"] >= min_competitors), key=_order)
    for fam in families:
        if len(chosen) >= count:
            break
        best = fam
        for level in ("relaxed", "exact"):      # narrow while no competitor brand is lost
            nested = {getattr(by_sku[sku], LEVEL_KEY[level]) for sku in best["skus"]}
            same = [by_key[k] for k in nested if k in by_key and by_key[k]["has_anchor"]
                    and by_key[k]["competitor_count"] == fam["competitor_count"]]
            if not same:
                break
            best = max(same, key=lambda g: (g["member_count"], g["label"]))
        take(best)

    for rank, g in enumerate(chosen[:count], start=1):
        g["featured_rank"] = rank
    return chosen[:count]


def build(products: dict[str, dict], anchor: str | None = None,
          min_competitors: int | None = None) -> tuple[list[Specs], list[dict]]:
    """Normalise every product and derive the groups at every level.

    Returns (specs for every product, groups). Products that are not eligible
    (Marketplace, open-box, inactive) are still normalised, so they can be
    inspected, but they never count towards a group.
    """
    anchor = canonical_brand(anchor or config.ANCHOR_BRAND) or "Lenovo"
    min_competitors = MIN_COMPETITORS if min_competitors is None else min_competitors
    specs = [normalise(p) for p in products.values()]
    harmonise_cpus(specs)

    members: dict[tuple[str, str], list[Specs]] = defaultdict(list)
    for s in specs:
        if not eligible(products[s.sku]):
            continue
        for level in LEVELS:
            key = getattr(s, LEVEL_KEY[level])
            if key:
                members[(level, key)].append(s)

    stamp = now_iso()
    groups = [_summarise(key, level, ms, anchor, min_competitors) for (level, key), ms in members.items()]
    for g in groups:
        g["created_at"] = stamp
    pinned = {sku for sku, p in products.items() if p.get("pinned")}
    pick_featured(groups, specs, pinned)
    groups.sort(key=lambda g: (LEVELS.index(g["level"]),) + _order(g))
    return specs, groups


def tracked_skus(products: dict[str, dict], specs: list[Specs], groups: list[dict]) -> list[str]:
    """SKUs the daily job fetches: eligible members of tracked groups at any level, plus pinned products."""
    tracked = {g["group_key"] for g in groups if g["tracked"]}
    out = {sku for sku, p in products.items() if p.get("pinned") and p.get("active", True)}
    for s in specs:
        if eligible(products[s.sku]) and tracked & {getattr(s, key) for key in LEVEL_KEY.values()}:
            out.add(s.sku)
    return sorted(out)


def unmatched_anchor_groups(groups: list[dict], level: str = "exact") -> list[dict]:
    """Anchor-brand configurations with no equivalent from another brand."""
    return [g for g in groups if g["level"] == level and g["has_anchor"] and g["competitor_count"] == 0]
