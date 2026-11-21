# Normalisation

Turns one product's raw spec table and title into typed, comparable fields. This is the part that decides whether two laptops match, and the part most likely to need changes when new listings appear.

Code: `tracker/normalise.py`. Tests: `tests/test_normalise.py`.

## Contract

```python
from tracker.normalise import normalise
specs = normalise(product)      # product: dict with sku, title, brand, raw_specs
```

`normalise` is pure: no I/O, no state, and the result depends only on that one product. It returns a `Specs` dataclass.

## Output fields

| Field | Example | In the exact key |
|---|---|---|
| `cpu_model` | `intel-226v` | yes |
| `memory_gb` | `16` | yes |
| `storage_gb` | `512`, `1024` | yes |
| `storage_type` | `ssd`, `ufs`, `emmc`, `hdd`, `hybrid` | yes |
| `form_factor` | `clamshell`, `convertible`, `detachable`, `dual-screen` | yes |
| `touchscreen` | `True` | yes |
| `os` | `windows`, `chromeos`, `googlebook`, `macos`, `linux`, `android`, `none` | yes |
| `gpu` | `integrated`, `nvidia-rtx-5070-ti` | yes |
| `screen_bucket` | `≤13`, `14`, `15–16`, `17+` | strict level only |
| `cpu_class` | `intel-core-ultra-5-s2` | class level only |
| `cpu_name`, `gpu_name` | "Intel Core Ultra 5 226V", "Intel Arc 130V" | no (display) |
| `brand` | `HP` (sub-brands folded in) | no (used to count competitors) |
| `model_family`, `model_number`, `color`, `os_edition`, `screen_size_in`, `resolution` | | no (reported) |
| `group_key`, `strict_key`, `relaxed_key`, `class_key` | 12 hex digits | see [Grouping](grouping.md) |
| `needs_review`, `review_notes` | | |
| `normaliser_ver` | `2026.11.4` | |

## The two sources

The **spec table** is the primary source. The **title** is the fallback when a row is missing, and a cross-check.

| Spec table says | Title says | Result |
|---|---|---|
| a value | nothing | the spec value |
| nothing | a value | the title value |
| CPU or GPU model A | a different model B | **held for review**: the listing contradicts itself on a key field |
| memory or storage A | a different B | A, with a note in `review_notes` |
| touchscreen: no | "Touchscreen" | yes, with a note (an explicit title beats a default-looking flag) |

Rows are found by display name with `_lookup(raw, name, preferred_groups)`. Values such as "Not provided", "N/A" and "None" count as missing.

## CPU

The hardest field, and the one that matters most.

**Identity is vendor + model number.** Best Buy's "Processor Model" row holds the series, and it is wrong too often to use: a Ryzen AI 7 350 filed under "AMD Ryzen 7 3000 Series", a Core Ultra 7 355 under "Series 1", the N355 under five different series names. The model number is nearly always right and is unique within a vendor, so `intel-355` is the identity. This is also what keeps `226V`, `228V` and `225U` apart.

`parse_cpu(vendor, series, number_field, title, cores)` works in four steps:

1. **Vendor** from the "Processor Brand" row (`Snapdragon` becomes `qualcomm`), else from keywords.
2. **Model number** from the "Processor Model Number" row with that vendor's patterns, else from the CPU part of the title. If both give a number and they differ, the laptop is held for review.
3. **Family and tier** for the display name, from the most specific text that states them: the number field, then the title, then the series.
4. **Class** (`cpu_class`): family + tier + generation.

The number field is free text. All of these are the same CPU:

| Raw value | Identity |
|---|---|
| `355`, `Ultra 7 355`, `U7 355`, `Processor 355` | `intel-355` |
| `350`, `IntelCore 7 350` | `intel-350` |
| `430`, `AI 5 430`, `5 430`, `Ryzen AI 5 430` | `amd-430` |
| `HX 370`, and `AI 9 HX470` | `amd-370`, `amd-470` |
| `X1-26-100`, `X1 26 100`, `X126100` | `qualcomm-x1-26-100` |
| `540`, `Kompanio 540`, and `K520` | `mediatek-540`, `mediatek-520` |
| `i9-14900HX` | `intel-14900hx` |

Rules worth knowing:

- **Things that are not model numbers are removed first** (`_prep_cpu`): "7000 Series", "Series 3", "13th Generation", "8-Core", and anything with a unit (GB, TB, Hz). A four-digit number from 2015 to 2035 with no suffix is a year.
- **The CPU part of a title** (`_cpu_segment`) starts at a vendor word and stops before memory, a separator, or a graphics name. A vendor word is preferred as the start because model lines are called "Galaxy Book6 Ultra 16" and "OmniBook Ultra 14".
- **AMD PRO chips share the identity of the plain chip.** "PRO" appears in some listings of a chip and not in others. It is kept in the display name.
- **The NVIDIA N1X is two CPUs.** It is sold with 18 and with 20 cores under one model number, $400 apart, so the core count joins the identity: `nvidia-n1x-18c`, `nvidia-n1x-20c`. A missing or contradictory core count means review.
- **A maker's part number in the CPU field is not a CPU.** Microsoft lists `EP2-77800` there on two laptops; they are held for review.
- **Intel N-series** are all named "Intel Processor N150", whatever the series row says.
- **No model number anywhere** (a row saying "Series 3", a title saying "Intel Core 5 Series 3") means review. It is never guessed.

`cpu_class` examples: `intel-core-ultra-7-s2` (first digit of a three-digit number is the generation), `intel-core-i5-g13`, `amd-ryzen-ai-7-400`, `amd-ryzen-7-7000`, `qualcomm-x1e` (Snapdragon X Elite). Naming schemes with no generations (N150, Celeron, Kompanio, N1X) keep the model as their class. Where a listing does not state the tier, the class is `None` here and is filled in by `grouping.harmonise_cpus` from other listings of the same model.

## GPU

`parse_gpu(graphics_type, graphics, title)`:

- **Integrated graphics are one value, `integrated`.** The same chip is listed as "Intel Arc", "Intel Arc 130V" and "Intel Graphics" by different brands, and integrated graphics follow from the CPU anyway.
- A **discrete model** found in the "Graphics" row or the title is kept: `nvidia-rtx-5060`, `nvidia-rtx-5070-ti`, `nvidia-rtx-pro-2000`, `nvidia-rtx-a500`, `amd-rx-7600m-xt`. Video memory is ignored.
- A listing that calls an integrated family "Dedicated" (several Dell listings do) is still `integrated`, with a note.
- "Dedicated" with no recognisable model means review.
- Intel Arc is discrete only when the listing says so and it is an A-series; Arc B390 is integrated.

## Memory and storage

- `"16 gigabytes"` becomes `16`. Terabytes are multiplied by 1024. Thousands separators are handled.
- **Storage is snapped** to the nearest of 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192 when within 8%. One terabyte is listed as "1000 gigabytes" on 123 laptops and "1024 gigabytes" on 20; both become `1024`.
- Storage type: `SSD, SSD` is `ssd`; an SSD plus a hard drive is `hybrid`.
- The title fallback is deliberately cautious. It returns nothing when a title lists two drives or an add-on ("512GB SSD + 1TB Dock"), and it does not take a graphics card's memory ("RTX 3050 6GB") for the system's.

## Form factor and touchscreen

Checked in this order:

1. **dual-screen**: title says "Dual Screen", "Zenbook Duo" or "Yoga Book".
2. **detachable**: title says "Detachable", "Surface Pro", "Flow Z13", "PZ13/PZ14", "Duet" or "Tablet".
3. **convertible**: the "2-in-1 Design" row is true, or the title says "2-in-1", "x360", "Flip", "Spin", "Book5 360", "Flex 5" or "ThinkPad ... Yoga". If such a laptop weighs **under two pounds** it is a detachable instead: no laptop with its keyboard attached is that light.
4. otherwise **clamshell**.

"Yoga" alone is not a signal: Yoga Slim and Yoga Pro are clamshells. The "2-in-1 Design" row is missing on 157 of 503 listings, which is why the title words matter.

Touchscreen comes from the "Touch Screen" row, then the title. A laptop that is not a clamshell is taken to have one.

## OS, screen and the rest

- OS edition is ignored: Windows 11 Home, Pro and S mode are all `windows`. "Chrome Plus OS" is `chromeos`; "Googlebook OS" is its own value.
- `screen_bucket` counts by whole inches: 13.3" and 13.8" are `≤13`, 14.5" is `14`, 15.6" and 16" share `15–16`.
- A resolution of 1900×1200 is a known typo for 1920×1200 and is corrected.
- `brand` folds sub-brands into the maker: HP OMEN is HP, Alienware is Dell.
- `model_family` is the words between the brand and the screen size in the title ("Yoga 7i 2-in-1", "Surface Laptop", "OMEN MAX"). Best effort; used only for display.
- Colour keeps the name before the comma ("Magnetite, Other" becomes "Magnetite").

## Review

`needs_review` is true exactly when the exact key could not be computed, that is, when a key field is `None`. Such a laptop is in no group. The reasons are in `review_notes`.

```
python build_groups.py --show review
```

Seven of 503 are in review: four with no CPU model number anywhere, two with Microsoft's part number in the CPU field, one whose spec table and title name different graphics cards.

Notes can also appear on laptops that *are* grouped. They record a disagreement that was resolved (for example integrated graphics labelled "Dedicated").

## Changing a rule

1. Add the failing case to the table in `tests/test_normalise.py`, with the real title and spec strings.
2. Change the rule. **Edit the file with an editor or the Write/Edit tools, not through a shell heredoc**: on this machine heredocs halve backslashes and silently corrupt regular expressions.
3. Bump `NORMALISER_VERSION`.
4. Diff the output for the whole collection before and after, and read every changed row. A fix that changes unrelated laptops is wrong.

   ```python
   from tracker.store import LocalStore
   from tracker import grouping
   before = LocalStore().product_specs()                       # what is stored now
   specs, groups = grouping.build(LocalStore().products())     # with the new rule
   changed = [(s.sku, f, before[s.sku].get(f), getattr(s, f)) for s in specs
              for f in ("cpu_model", "memory_gb", "storage_gb", "storage_type", "form_factor",
                        "touchscreen", "os", "gpu") if before[s.sku].get(f) != getattr(s, f)]
   ```
5. `pytest`, then `python build_groups.py` to store and sync the result.
6. Update the counts in the README if the groups changed.

## Known limits

- Detachables and dual-screen laptops are recognised by short word lists. A new product line that uses none of the words is classed as a convertible or clamshell.
- The title-only path matters for listings without a spec table (Marketplace offers). It is tested on examples but has not been audited on a large sample.
- Equivalence is on paper. Panel quality, battery, weight, build and warranty are not compared.
