"""Shared helpers: build a collected-product dict the way the Best Buy collector stores it."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_SKU = [6000000]


def make_product(title: str, brand: str, *, sku: str | None = None, cpu_brand: str | None = None,
                 series: str | None = None, number: str | None = None, ram: str | None = None,
                 storage: str | None = None, storage_type: str | None = None, graphics: str | None = None,
                 graphics_type: str | None = None, os: str | None = None, touch: str | None = None,
                 two_in_one: str | None = None, size: str | None = None, resolution: str | None = None,
                 seller_class: str = "1P", condition: str = "new", pinned: bool = False, **extra) -> dict:
    """A product record with a Best Buy style spec table: {group: {display name: value}}."""
    def rows(**kv):
        return {k: v for k, v in kv.items() if v is not None}

    raw = {
        "Processor": rows(**{"Processor Brand": cpu_brand, "Processor Model": series,
                             "Processor Model Number": number}),
        "Memory": rows(**{"System Memory (RAM)": ram}),
        "Storage": rows(**{"Total Storage Capacity": storage, "Storage Type": storage_type}),
        "Graphics": rows(**{"Graphics": graphics, "Graphics Type": graphics_type}),
        "Compatibility": rows(**{"Operating System": os}),
        "Display": rows(**{"Touch Screen": touch, "Screen Size": size, "Screen Resolution": resolution}),
        "Features": rows(**{"2-in-1 Design": two_in_one}),
    }
    raw = {group: values for group, values in raw.items() if values}
    if sku is None:
        _SKU[0] += 1
        sku = str(_SKU[0])
    product = {"sku": sku, "title": title, "brand": brand, "raw_specs": raw or None,
               "seller_class": seller_class, "condition": condition, "pinned": pinned, "active": True}
    product.update(extra)
    return product


@pytest.fixture
def product():
    return make_product
