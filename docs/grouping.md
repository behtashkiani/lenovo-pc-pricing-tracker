# Grouping

Forms comparison groups from the normalised laptops, decides which are tracked, and picks the featured ones.

Code: `tracker/grouping.py`, the field tuples at the top of `tracker/normalise.py`, `build_groups.py`. Tests: `tests/test_grouping.py`.

## Which laptops count

`grouping.eligible(product)`: active, sold by Best Buy (`seller_class == "1P"`), condition `new`. Everything else is still normalised, so it can be inspected, but never counts towards a group and is never tracked.

## The four match levels

Each level contains the one before it: a strict group lies inside an exact group, which lies inside a relaxed group, which lies inside a class group.

| Level | Two laptops match when they have the same... | May differ in |
|---|---|---|
| **strict** | everything in exact, plus the screen size bucket | colour, OS edition, resolution |
| **exact** | CPU model, memory, storage size, storage type, form factor, touchscreen, OS, GPU | also screen size |
| **relaxed** | CPU model, memory, storage size, form factor, OS, GPU | also storage type and touchscreen |
| **class** | CPU class, memory, storage size, form factor, OS, GPU | also the exact CPU model |

```python
KEY_FIELDS     = (cpu_model, memory_gb, storage_gb, storage_type, form_factor, touchscreen, os, gpu)
STRICT_FIELDS  = KEY_FIELDS + (screen_bucket,)
RELAXED_FIELDS = (cpu_model, memory_gb, storage_gb, form_factor, os, gpu)
CLASS_FIELDS   = (cpu_class, memory_gb, storage_gb, form_factor, os, gpu)
```

**Exact is the baseline** and matches the brief: its example puts a 14" HP with 16" Lenovo and Dell models, so screen size is not part of it.

**Class** exists because Best Buy sells no identical configuration from more than three other brands. A CPU class is family + tier + generation (`intel-core-ultra-7-s2` covers the 255U, 255H, 256V, 258V and so on). Chips in one class can differ considerably in performance, so it is a market-tier comparison, always labelled as such, with each laptop's own CPU shown next to it.

**Strict** is the narrow end, for comparing like-sized laptops. A laptop with no screen size has no strict group and is grouped from exact upwards.

### Keys

```python
group_key = sha1("|".join(map(str, values))).hexdigest()[:12]
```

The exact key is the bare hash. The other three are salted with their level name, so keys of different levels never collide. A key is `None` if any of its fields is `None`. Each laptop carries four keys: `strict_key`, `group_key` (exact), `relaxed_key`, `class_key`.

For the class key a CPU with no class of its own falls back to its model, which keeps the class level a superset of the relaxed level.

### Labels

Generated from the fields by `normalise.group_label`:

| Level | Example |
|---|---|
| strict | `Core Ultra 5 226V · 16 GB · 512 GB SSD · Convertible touch · 15–16" · Windows` |
| exact | `Core Ultra 5 226V · 16 GB · 512 GB SSD · Convertible touch · Windows` |
| relaxed | `Core Ultra 7 355 · 32 GB · 1 TB · Clamshell · Windows` |
| class | `Core Ultra 7 Series 2 · 16 GB · 512 GB · Clamshell · Windows` |

A discrete GPU is added after the CPU: `AMD Ryzen 7 250 · RTX 5060 · 16 GB · ...`.

## Harmonising CPUs

`harmonise_cpus(specs)` is the one step that looks across products. For each CPU model it gives every listing:

- the **class** most of its listings state. A listing can only state its own class when it names the tier ("Ryzen AI 7"); some do not, and a few name the wrong family ("Ryzen 7 445" for a Ryzen AI 7 445).
- the **display name** most of its listings produce, keeping "PRO" on the listings that said so.

A model with no class at all keeps the model as its class. Identity (`cpu_model`) is never touched. Keys are recomputed afterwards.

## Tracked groups

A group is **tracked** when it contains the anchor brand and at least `MIN_COMPETITORS` other brands (default 1). Brands are counted by maker: HP OMEN is HP, Alienware is Dell.

`tracked_skus` is every eligible laptop in a tracked group at any level, plus every pinned laptop. That is what the daily job prices. The same rule is implemented three times and the tests keep them equal:

- `grouping.tracked_skus` (from fresh results)
- `LocalStore.tracked_skus` (from the stored files)
- the `tracked_skus` view in `sql/schema.sql`

## Featured groups

The ten groups the dashboard opens with. `pick_featured` chooses them deterministically:

1. **Pinned laptops first.** The exact group of every pinned laptop, provided it contains the anchor brand.
2. **Then one group per class-level family**, taking families with at least `FEATURED_MIN_COMPETITORS` other brands (default 2), ordered by number of competitor brands, then size.
3. **Each family is shown at the narrowest level that keeps all its competitor brands.** Starting from the class group, it narrows to a relaxed group, then an exact group, as long as one exists with the same number of competitors. Widening is used only where it adds a brand.
4. **Featured groups never share a laptop.**
5. Ranks run 1 to `FEATURED_COUNT` (default 10). The strict level is never featured.

## What the data gives (21 Nov 2026)

503 laptops, anchor Lenovo.

| Level | Groups | With a Lenovo | + ≥1 other brand | ≥2 | ≥3 | ≥4 | ≥5 |
|---|---|---|---|---|---|---|---|
| strict | 402 | 111 | 18 | 3 | 0 | 0 | 0 |
| exact | 330 | 93 | 25 | 6 | 0 | 0 | 0 |
| relaxed | 286 | 86 | 35 | 12 | 3 | 0 | 0 |
| class | 238 | 80 | 38 | 18 | 9 | 3 | 1 |

116 groups are tracked, covering 174 laptops.

Read this as the ceiling of the retailer, not of the method: no identical configuration is sold by more than three other brands, and five competitors exists once, at the class level.

Featured:

| # | Level | Group | Other brands |
|---|---|---|---|
| 1 | exact | Core Ultra 5 226V · 16 GB · 512 GB SSD · Convertible touch · Windows | HP (pinned: the brief's example) |
| 2 | class | Core Ultra 7 Series 2 · 16 GB · 512 GB · Clamshell · Windows | Acer, Dell, HP, LG, Samsung |
| 3 | class | Core Ultra 5 Series 3 · 16 GB · 512 GB · Clamshell · Windows | Acer, Dell, HP, Samsung |
| 4 | class | Core Ultra 7 Series 2 · 32 GB · 1 TB · Clamshell · Windows | ASUS, Acer, Dell, Samsung |
| 5 | relaxed | Snapdragon X X1-26-100 · 8 GB · 256 GB · Clamshell · Windows | ASUS, HP, Samsung |
| 6 | class | Core 5 Series 3 · 8 GB · 256 GB · Clamshell · Windows | Dell, HP, LG |
| 7 | relaxed | Core Ultra 7 355 · 32 GB · 1 TB · Clamshell · Windows | ASUS, Acer, LG |
| 8 | class | AMD Ryzen AI 7 400 Series · 16 GB · 512 GB · Clamshell · Windows | Acer, Dell, HP |
| 9 | relaxed | Snapdragon X X1-26-100 · 16 GB · 256 GB · Clamshell · Windows | ASUS, HP, Samsung |
| 10 | class | AMD Ryzen 7 7000 Series · 16 GB · 512 GB · Clamshell · Windows | Acer, Dell, HP |

### The brief's example

The Lenovo Yoga 7i 2-in-1 16" (SKU 6615769) and the HP OmniBook X Flip 14" (SKU 6613868) form one exact group, although the Lenovo's title never names its CPU model. Both are pinned, so the group is featured first.

**The Dell is missing because Best Buy does not sell it.** No Dell Plus 2-in-1 with the Core Ultra 5 226V is listed as new, open-box, refurbished or Marketplace, and Honey's Best Buy catalogue has none either. Dell sells that line with the Core Ultra 7 256V, which groups with Lenovo's Yoga 7i 256V. A test built from Dell's real listing format shows that the three example laptops do form one group when all are present.

## Commands

```
python build_groups.py                           # rebuild, print the summary, sync
python build_groups.py --show featured           # featured groups with their members and prices
python build_groups.py --show tracked --level class
python build_groups.py --show unmatched          # Lenovo configurations with no other brand
python build_groups.py --show review
python build_groups.py --anchor HP               # the same collection from another brand's point of view
```

Run it after any collect or rule change. It reads the stored spec tables, so nothing is scraped.

## Settings

| Variable | Default | Effect |
|---|---|---|
| `ANCHOR_BRAND` | `Lenovo` | Whose laptops define the groups |
| `MIN_COMPETITORS` | `1` | Other brands a group needs to be tracked |
| `FEATURED_COUNT` | `10` | How many featured groups |
| `FEATURED_MIN_COMPETITORS` | `2` | Other brands a family needs to be featured |

## Changing a level

The field tuples in `normalise.py` are the definition. Changing one changes the keys of every laptop at that level, so group keys are not stable across such a change (the groups table is rewritten in full on every build, and nothing else references a group key). After a change: bump `NORMALISER_VERSION`, run the tests, rebuild, and update `LEVEL_TEXT` in `app/app_pages/groups.py` and `FEATURE_FIELDS` in `tracker/dashboard.py`, which describe the levels to the reader.
