"""Spec normaliser: Best Buy's raw specification table + title -> typed, comparable fields.

Pure and deterministic: `normalise(product)` depends only on the product's own
title, brand and raw spec table, so the same input always gives the same output and
every grouping decision can be explained by pointing at the fields.

What the real data forced (503 Best Buy-sold laptops, Nov 2026):

* The spec table is the primary source; the title is the fallback and a cross-check.
  Titles often omit the CPU model ("Intel Core Ultra 5 Processor 2024").
* "Processor Model" (the series) is unreliable: a Ryzen AI 7 350 is filed under
  "AMD Ryzen 7 3000 Series", a Core Ultra 7 355 under "Series 1". The CPU identity is
  therefore vendor + model number ("Processor Model Number"), which is unique within
  a vendor. Series and title only supply the display name.
* "Processor Model Number" is free text: '226V', 'Ultra 5 325', 'U7 355',
  'IntelCore 7 350', 'AI 5 430', 'HX 370', 'X126100', 'Not provided', 'Series 3'.
* Integrated graphics are named inconsistently for the same chip ('Intel Arc',
  'Intel Arc 130V', 'Intel Graphics'), so the comparable value is `integrated`
  unless a discrete GPU model is present.
* 1 TB is listed as both '1000 gigabytes' and '1024 gigabytes'.

A key field that cannot be parsed is None and the product is flagged needs_review;
it is then never grouped automatically. Wrong groups are worse than missing ones.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field

NORMALISER_VERSION = "2026.11.4"

# Fields that must be identical for two laptops to be equivalent.
KEY_FIELDS = ("cpu_model", "memory_gb", "storage_gb", "storage_type",
              "form_factor", "touchscreen", "os", "gpu")
# Strict level: exact, plus the same screen size bucket.
STRICT_FIELDS = KEY_FIELDS + ("screen_bucket",)
# Relaxed level: same processor model, ignoring storage type and touchscreen.
RELAXED_FIELDS = ("cpu_model", "memory_gb", "storage_gb", "form_factor", "os", "gpu")
# Class level: same processor class (family, tier, generation) instead of the same model.
CLASS_FIELDS = ("cpu_class", "memory_gb", "storage_gb", "form_factor", "os", "gpu")
LEVELS = ("strict", "exact", "relaxed", "class")        # strictest first: each widens the one before
LEVEL_FIELDS = {"strict": STRICT_FIELDS, "exact": KEY_FIELDS, "relaxed": RELAXED_FIELDS, "class": CLASS_FIELDS}
LEVEL_KEY = {"strict": "strict_key", "exact": "group_key", "relaxed": "relaxed_key", "class": "class_key"}

# Screen size buckets of the strict level: (inches below which a screen falls in the bucket, bucket).
# A size counts by its whole inches, as the size classes are sold: 13.3" and 13.8" are 13-inch
# laptops, 14.5" is a 14, 15.6" and 16" share a bucket.
_SCREEN_BUCKETS = ((14, "≤13"), (15, "14"), (17, "15–16"))
_LARGEST_SCREEN_BUCKET = "17+"
SCREEN_BUCKETS = tuple(name for _, name in _SCREEN_BUCKETS) + (_LARGEST_SCREEN_BUCKET,)

# Best Buy lists some sub-brands as separate brands; competitors are counted by maker.
BRAND_ALIASES = {"hp omen": "HP", "omen": "HP", "alienware": "Dell", "lg gram": "LG",
                 "gigabyte": "GIGABYTE", "aorus": "GIGABYTE", "rog": "ASUS",
                 "republic of gamers": "ASUS", "microsoft surface": "Microsoft"}

_STORAGE_SIZES = (16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192)

# Listed resolutions that are data-entry errors for a real panel.
_RESOLUTION_FIXES = {"1900x1200": "1920x1200"}


@dataclass
class Specs:
    sku: str
    brand: str | None = None            # maker (sub-brands folded in)
    model_family: str | None = None
    model_number: str | None = None     # manufacturer part number
    cpu_model: str | None = None        # KEY  identity: vendor-modelnumber, e.g. intel-226v
    cpu_name: str | None = None         # display, e.g. Intel Core Ultra 5 226V
    cpu_class: str | None = None        # family + tier + generation, e.g. intel-core-ultra-5-s2
    memory_gb: int | None = None        # KEY
    storage_gb: int | None = None       # KEY  512, 1024 (= 1 TB), ...
    storage_type: str | None = None     # KEY  ssd | emmc | ufs | hdd | hybrid
    form_factor: str | None = None      # KEY  clamshell | convertible | detachable | dual-screen
    touchscreen: bool | None = None     # KEY
    os: str | None = None               # KEY  windows | chromeos | macos | googlebook | linux | ...
    os_edition: str | None = None       # reported only: home | pro | s
    gpu: str | None = None              # KEY  integrated | nvidia-rtx-5060 | ...
    gpu_name: str | None = None         # display, e.g. Intel Arc 130V
    screen_size_in: float | None = None
    screen_bucket: str | None = None    # strict level only: ≤13 | 14 | 15–16 | 17+
    resolution: str | None = None       # 1920x1200
    color: str | None = None
    group_key: str | None = None        # exact level; None until every key field parsed
    strict_key: str | None = None       # None as well when the screen size is unknown
    relaxed_key: str | None = None
    class_key: str | None = None
    needs_review: bool = False
    review_notes: list[str] = field(default_factory=list)
    normaliser_ver: str = NORMALISER_VERSION

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- helpers
def _lookup(raw: dict | None, name: str, groups: tuple[str, ...] = ()) -> str | None:
    """Value of a spec row by display name: preferred groups first, then any group."""
    if not raw:
        return None
    wanted = name.lower()
    ordered = [g for g in groups if g in raw] + [g for g in raw if g not in groups]
    for g in ordered:
        for k, v in (raw.get(g) or {}).items():
            if k.lower() == wanted and v is not None:
                text = str(v).strip()
                if text and text.lower() not in ("not provided", "not applicable", "n/a", "none", "other", "-"):
                    return text
    return None


def _clean_text(text: str | None) -> str:
    if not text:
        return ""
    t = text.replace("™", "").replace("®", "").replace(" ", " ")
    t = re.sub(r"[¹²³†‡*]", "", t)          # footnote marks: "Copilot+ PC¹"
    t = re.sub(r"\((?:R|TM|tm|r)\)", "", t)
    t = t.replace("–", "-").replace("—", "-")
    t = t.replace(chr(92), "")           # a few titles carry a literal backslash before the inch mark
    return re.sub(r"\s+", " ", t).strip()


def _bool(value: str | None) -> bool | None:
    if value is None:
        return None
    v = value.strip().lower()
    if v in ("true", "yes", "y", "1"):
        return True
    if v in ("false", "no", "n", "0"):
        return False
    return None


def canonical_brand(brand: str | None) -> str | None:
    if not brand or not brand.strip():
        return None
    b = brand.strip()
    return BRAND_ALIASES.get(b.lower(), b)


def make_key(values: tuple, level: str = "exact") -> str | None:
    """12-hex-digit key over the given field values; None if any value is missing.
    Every level but exact salts its key with the level, so the keys never collide."""
    if any(v is None for v in values):
        return None
    text = "|".join(map(str, values))
    if level != "exact":
        text = f"{level}|{text}"
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def screen_bucket(size_in: float | None) -> str | None:
    """Screen size bucket of the strict level: 13.3 -> '≤13', 14.5 -> '14', 16 -> '15–16', 17.3 -> '17+'."""
    if size_in is None:
        return None
    return next((name for below, name in _SCREEN_BUCKETS if size_in < below), _LARGEST_SCREEN_BUCKET)


def assign_keys(s: "Specs") -> None:
    """(Re)compute the group keys of every level from the normalised fields."""
    s.group_key = make_key(tuple(getattr(s, f) for f in KEY_FIELDS))
    s.strict_key = make_key(tuple(getattr(s, f) for f in STRICT_FIELDS), "strict")
    s.relaxed_key = make_key(tuple(getattr(s, f) for f in RELAXED_FIELDS), "relaxed")
    # A CPU with no class of its own (unknown tier) falls back to its model, so the
    # class level always contains the relaxed level.
    s.class_key = make_key(tuple((s.cpu_class or s.cpu_model) if f == "cpu_class" else getattr(s, f)
                                 for f in CLASS_FIELDS), "class")
    s.needs_review = s.group_key is None


# --------------------------------------------------------------------------- CPU
# Only consulted when the listing has no "Processor Brand" row. Order matters: the
# Intel pattern's bare "Ultra <digit>" would otherwise claim "Kompanio Ultra 910".
_VENDORS = (
    ("mediatek", re.compile(r"\b(mediatek|kompanio|MT\d{4})", re.I)),
    ("qualcomm", re.compile(r"\b(snapdragon|qualcomm)", re.I)),
    ("nvidia", re.compile(r"\b(nvidia\s+rtx\s+spark|N1X)\b", re.I)),
    ("apple", re.compile(r"\b(apple|M\d\s*(pro|max|ultra)?\s*chip)", re.I)),
    ("intel", re.compile(r"\b(intel|celeron|pentium|core\s*(ultra|i\d|\d)|ultra\s*x?\d|xeon)", re.I)),
    ("amd", re.compile(r"\b(amd|ryzen|athlon)", re.I)),
)
_VENDOR_SPEC = {"intel": "intel", "amd": "amd", "snapdragon": "qualcomm", "qualcomm": "qualcomm",
                "mediatek": "mediatek", "apple": "apple", "nvidia": "nvidia"}
_VENDOR_LABEL = {"intel": "Intel", "amd": "AMD", "qualcomm": "Snapdragon", "mediatek": "MediaTek",
                 "apple": "Apple", "nvidia": "NVIDIA"}

# A 4-digit number with no letter suffix in 2015-2035 is a year, never a model number.
_YEAR = re.compile(r"^20(1[5-9]|2\d|3[0-5])$")


def _prep_cpu(text: str | None) -> str:
    """Upper-cased CPU text with the known spelling quirks ironed out."""
    t = _clean_text(text).upper()
    t = re.sub(r"\bAL\b", "AI", t)                        # "Ryzen Al 7" (lower-case L)
    t = t.replace("INTELCORE", "INTEL CORE")
    t = re.sub(r"\bULTRA\s*-?\s*(X?\d)\b", r"ULTRA \1", t)  # Ultra7, Ultra-7
    t = re.sub(r"\b\d{2,5}[A-Z]?\s*SERIES\b", " ", t)     # "7000 Series", "300 Series", "200V Series"
    t = re.sub(r"\bSERIES\s*\d\b", " ", t)                # "Series 3"
    t = re.sub(r"\b\d{1,2}(ST|ND|RD|TH)\s+GEN(ERATION)?\b", " ", t)
    t = re.sub(r"\b\d+\s*-?\s*CORES?\b", " ", t)          # "(8-Core CPU)", "12 Core"
    t = re.sub(r"\b\d+(\.\d+)?\s?(GB|TB|MB|GHZ|MHZ|HZ|WH|NITS)\b", " ", t)   # never a model number
    return re.sub(r"\s+", " ", t).strip()


def _intel_number(t: str) -> str | None:
    for pattern in (
        r"\bI[3579][-\s]?(N?\d{3,5}[A-Z]{0,2}\d?)\b",     # i7-13620H, i3-N305, i5-1235U
        r"\b([NU]\d{2,4})\b",                             # N50, N150, N355, N4020, U300
        r"\b(\d{4,5}[A-Z]{1,2}\d?)\b",                    # 1335U, 13620H, 14900HX, 1115G4
        r"\b(\d{3}[A-Z]{1,2})\b",                         # 226V, 255H, 275HX, 100U
        r"(?<![\d.])(\d{3})\b(?!\s*(GB|TB|HZ|NITS|WH?)\b)",  # 350, 320, 304
    ):
        m = re.search(pattern, t)
        if m and not _YEAR.match(m.group(1)):
            return m.group(1)
    return None


def _amd_number(t: str) -> str | None:
    m = re.search(r"\b(\d{4}[A-Z]{1,2}(?:3D)?)\b", t)      # 7535HS, 8745HX, 5825U, 9955HX3D
    if m:
        return m.group(1)
    m = re.search(r"\bHX\s*-?\s*(\d{3})\b", t)             # AI 9 HX 370, HX470
    if m:
        return m.group(1)
    m = re.search(r"\bAI\s+(\d)(\d{3})\b", t)              # "AI 7345" = AI 7 345 (missing space)
    if m and not _YEAR.match(m.group(1) + m.group(2)):     # ...but "AI 2026" is a year
        return m.group(2)
    for m in re.finditer(r"(?<![\d.])(\d{2,3})\b(?!\s*(GB|TB|HZ|NITS|WH?)\b)", t):
        return m.group(1)                                 # 40, 125, 250, 350, 445
    return None


def _qualcomm_number(t: str) -> str | None:
    m = re.search(r"\bX(\d)([EP]?)[-\s]?(\d{2})[-\s]?(\d{3})\b", t)   # X1E-78-100, X126100, X1 26 100
    if m:
        return f"X{m.group(1)}{m.group(2)}-{m.group(3)}-{m.group(4)}"
    return None


def _mediatek_number(t: str) -> str | None:
    m = re.search(r"\b(MT\d{4})", t)
    if m:
        return m.group(1)
    m = re.search(r"\bK?(\d{3,4})\b", t)                   # Kompanio 540, K520, 910
    if m and not _YEAR.match(m.group(1)):
        return m.group(1)
    return None


def _apple_number(t: str) -> str | None:
    m = re.search(r"\bM(\d)\s*(PRO|MAX|ULTRA)?\b", t)
    if m:
        return f"M{m.group(1)}" + (f"-{m.group(2)}" if m.group(2) else "")
    return None


def _nvidia_number(t: str) -> str | None:
    m = re.search(r"\b(N\d[A-Z]?)\b", t)
    return m.group(1) if m else None


_NUMBER_PARSERS = {"intel": _intel_number, "amd": _amd_number, "qualcomm": _qualcomm_number,
                   "mediatek": _mediatek_number, "apple": _apple_number, "nvidia": _nvidia_number}

_CPU_WORDS = {"INTEL", "AMD", "CORE", "ULTRA", "RYZEN", "AI", "PRO", "PROCESSOR", "CPU", "SNAPDRAGON",
              "QUALCOMM", "MEDIATEK", "KOMPANIO", "NVIDIA", "RTX", "SPARK", "APPLE", "CHIP", "MAX",
              "PLUS", "ELITE", "EXTREME", "PLATFORM", "OCTA", "HEXA", "QUAD", "DUAL", "EVO", "EDITION",
              "VPRO", "TECHNOLOGY", "WITH", "KRACKAN", "NOT", "PROVIDED", "SERIES", "MOBILE", "N"}


def _generic_number(t: str) -> str | None:
    """Unknown naming scheme: the last token that looks like a model code."""
    tokens = [x for x in re.split(r"[\s,/()]+", t) if x and x not in _CPU_WORDS]
    for tok in reversed(tokens):
        if re.search(r"\d", tok) and re.fullmatch(r"[A-Z0-9][A-Z0-9+\-]{1,13}", tok) and not _YEAR.match(tok):
            if not re.fullmatch(r"\d", tok):
                return tok
    return None


def _cpu_name_parts(vendor: str, t: str) -> tuple[str | None, str | None, bool]:
    """(line, tier, pro) read from one piece of CPU text, e.g. ('Core Ultra', '5', False)."""
    pro = bool(re.search(r"\bPRO\b", t)) and vendor == "amd"
    if vendor == "intel":
        m = re.search(r"\bULTRA\s*(X?\d)\b", t) or re.search(r"\bU([579])\b", t)
        if m:
            return "Core Ultra", m.group(1), False
        m = re.search(r"\bX([79])\b", t)
        if m:
            return "Core Ultra", "X" + m.group(1), False
        m = re.search(r"\bI([3579])\b", t)
        if m:
            return "Core", "i" + m.group(1), False
        if "CELERON" in t:
            return "Celeron", None, False
        if "PENTIUM" in t:
            return "Pentium", None, False
        m = re.search(r"\bCORE\s+(\d)\b", t)
        if m:
            return "Core", m.group(1), False
        if "ULTRA" in t:
            return "Core Ultra", None, False
        return None, None, False
    if vendor == "amd":
        if re.search(r"\bAI\s+MAX\s*\+", t) or re.search(r"\bMAX\s*\+", t):
            return "Ryzen AI Max+", None, pro
        if re.search(r"\bAI\s+MAX\b", t):
            return "Ryzen AI Max", None, pro
        m = re.search(r"\bAI\s+(\d)\b", t)
        if m:
            return "Ryzen AI", m.group(1), pro
        if re.search(r"\bAI\b", t):
            return "Ryzen AI", None, pro
        m = re.search(r"\bRYZEN\s+(\d)\b", t) or re.search(r"\bR(\d)\b", t)
        if m:
            return "Ryzen", m.group(1), pro
        if "ATHLON" in t:
            return "Athlon", None, pro
        if "RYZEN" in t:
            return "Ryzen", None, pro
        return None, None, pro
    if vendor == "qualcomm":
        m = re.search(r"\bX(\d)([EP]?)[-\s]?\d{2}[-\s]?\d{3}\b", t)   # the model number names its family
        if m:
            gen = "" if m.group(1) == "1" else m.group(1)
            extreme = " Extreme" if "EXTREME" in t else ""
            return f"X{gen}" + {"E": " Elite", "P": " Plus", "": ""}[m.group(2)] + extreme, None, False
        m = re.search(r"\bX(\d)?\s*-?\s*(ELITE EXTREME|ELITE|PLUS)\b", t)
        if m:
            return f"X{m.group(1) or ''} {m.group(2).title()}", None, False
        if re.search(r"\bSNAPDRAGON\s+X\b", t):
            return "X", None, False
        return None, None, False
    if vendor == "mediatek":
        if "KOMPANIO ULTRA" in t:
            return "Kompanio Ultra", None, False
        if "KOMPANIO" in t:
            return "Kompanio", None, False
        return None, None, False
    if vendor == "nvidia":
        return "RTX Spark", None, False
    return None, None, False


_CPU_ANCHOR_VENDOR = re.compile(r"(?i)\b(intel|amd|ryzen|snapdragon|qualcomm|mediatek|kompanio|celeron|pentium|"
                                r"athlon|nvidia rtx spark|apple m\d)")
_CPU_ANCHOR_BARE = re.compile(r"(?i)\b(core\s*(ultra|i\d|\d\b)|ultra\s*x?\d\b|U[579]-\d)")
_CPU_STOP = re.compile(r"(?i)[\s-]\d+\s?(GB|TB)\b|\swith\s|\s-\s|-\s|\s-|,|\bGeForce\b|\bRadeon\b|\bRTX\s?\d|"
                       r"\bIntel (Arc|Graphics|UHD|Iris)")


def _cpu_segment(title: str) -> str:
    """The part of a title that talks about the processor (stops before memory/GPU).
    A vendor word is preferred as the anchor: 'Galaxy Book6 Ultra 16' and 'OmniBook
    Ultra 14' carry a bare 'Ultra <digit>' that is not a CPU."""
    t = _clean_text(title)
    m = _CPU_ANCHOR_VENDOR.search(t) or _CPU_ANCHOR_BARE.search(t)
    if not m:
        return ""
    seg = t[m.start():]
    stop = _CPU_STOP.search(seg, 1)
    return seg[:stop.start()] if stop else seg


def cpu_class(vendor: str, number: str, line: str | None, tier: str | None) -> str | None:
    """Processor class: family + tier + generation, e.g. intel-core-ultra-7-s2.

    This is what Best Buy's "Processor Model" field is meant to hold, derived from
    the model number instead because that field is unreliable. Naming schemes with
    no generations (N150, Celeron, Kompanio, N1X) keep the identity as their class.
    Returns None when the scheme needs a tier this listing does not state; the
    grouper then takes the class from other listings of the same model number.
    """
    n = number.upper()
    if vendor == "intel":
        m = re.fullmatch(r"(\d)\d\d[A-Z]{0,2}", n)                 # Core / Core Ultra: 226V, 350, 275HX
        if m:
            if line in ("Core Ultra", "Core") and tier and not tier.startswith("i"):
                return f"intel-{line.lower().replace(' ', '-')}-{tier.lower()}-s{m.group(1)}"
            return None
        m = re.fullmatch(r"(\d{4,5})[A-Z]{1,2}\d?", n)              # Core i: 1335U, 13620H, 1115G4, 8250U
        if m:
            digits = m.group(1)                                     # generation: 10-14 take two digits, 4-9 one
            gen = digits[:2] if len(digits) == 5 or digits[:2] in ("10", "11", "12", "13", "14") else digits[0]
            return f"intel-core-{tier}-g{gen}" if tier and tier.startswith("i") else None
        return f"intel-{n.lower()}"
    if vendor == "amd":
        family = {"Ryzen AI": "ryzen-ai", "Ryzen": "ryzen", "Ryzen AI Max+": "ryzen-ai-max",
                  "Ryzen AI Max": "ryzen-ai-max"}.get(line or "")
        m = re.fullmatch(r"(\d)\d\d", n)                            # 350, 445, 388
        if m:
            if family == "ryzen-ai-max":
                return f"amd-ryzen-ai-max-{m.group(1)}00"
            return f"amd-{family}-{tier}-{m.group(1)}00" if family and tier else None
        m = re.fullmatch(r"(\d)\d{3}[A-Z]{1,2}(?:3D)?", n)          # 7535HS, 8840U, 9955HX3D
        if m:
            return f"amd-{family}-{tier}-{m.group(1)}000" if family and tier else None
        return f"amd-{n.lower()}"
    if vendor == "qualcomm":
        m = re.fullmatch(r"X(\d)([EP]?)-\d\d-\d{3}", n)             # X1E-80-100 -> Snapdragon X Elite
        return f"qualcomm-x{m.group(1)}{m.group(2).lower()}" if m else f"qualcomm-{n.lower()}"
    return f"{vendor}-{n.lower()}"


def cpu_class_label(cls: str) -> str | None:
    """intel-core-ultra-7-s2 -> 'Core Ultra 7 Series 2'; None when the class is just the model."""
    m = re.fullmatch(r"intel-core(-ultra)?-(x?\d)-s(\d)", cls)
    if m:
        return f"Core {'Ultra ' if m.group(1) else ''}{m.group(2).upper()} Series {m.group(3)}"
    m = re.fullmatch(r"intel-core-(i\d)-g(\d+)", cls)
    if m:
        return f"Core {m.group(1)} {m.group(2)}th Gen"
    m = re.fullmatch(r"amd-ryzen(-ai)?-(\d)-(\d+)", cls)
    if m:
        return f"AMD Ryzen {'AI ' if m.group(1) else ''}{m.group(2)} {m.group(3)} Series"
    m = re.fullmatch(r"amd-ryzen-ai-max-(\d+)", cls)
    if m:
        return f"AMD Ryzen AI Max {m.group(1)} Series"
    m = re.fullmatch(r"qualcomm-x(\d)([ep]?)", cls)
    if m:
        gen = "" if m.group(1) == "1" else m.group(1)
        return f"Snapdragon X{gen}" + {"e": " Elite", "p": " Plus", "": ""}[m.group(2)]
    m = re.fullmatch(r"nvidia-(n\d[a-z]?)", cls)
    if m:
        return f"NVIDIA RTX Spark {m.group(1).upper()}"
    return None


@dataclass
class Cpu:
    model: str | None = None        # identity, e.g. intel-226v
    name: str | None = None         # display, e.g. Intel Core Ultra 5 226V
    cls: str | None = None          # class, e.g. intel-core-ultra-5-s2
    notes: list[str] = field(default_factory=list)


def parse_cpu(vendor_spec: str | None, series: str | None, number_field: str | None,
              title: str | None, cores: str | None = None) -> Cpu:
    """Fuse the places a CPU is described: the model-number field (identity), the
    title (fallback and cross-check) and the series field (display name only).
    `cores` is the "Number of CPU Cores" row, needed where one model number is sold
    in two core counts (NVIDIA N1X)."""
    segment = _cpu_segment(title or "")
    field_t, series_t, title_t = _prep_cpu(number_field), _prep_cpu(series), _prep_cpu(segment)

    vendor = _VENDOR_SPEC.get((vendor_spec or "").strip().lower())
    if not vendor:
        for name, pattern in _VENDORS:
            if any(pattern.search(x or "") for x in (series, number_field, segment)):
                vendor = name
                break
    if not vendor:
        return Cpu(notes=["cpu: vendor not recognised"])

    parser = _NUMBER_PARSERS[vendor]
    from_field = parser(field_t) if field_t else None
    if not from_field and field_t and vendor in ("qualcomm", "mediatek"):
        from_field = _generic_number(field_t)   # schemes with no pattern of their own, e.g. "C Platform C1"
        if from_field and re.fullmatch(r"[A-Z0-9]{2,4}-\d{4,}", from_field):
            from_field = None                   # 'EP2-77800' is the maker's part number, not a CPU
    from_title = parser(title_t) if title_t else None

    number = from_field or from_title
    if not number:
        return Cpu(notes=[f"cpu: no model number (series={series!r}, number={number_field!r})"])
    if from_field and from_title and from_field != from_title:
        # A listing that names two different CPUs is not grouped on either of them.
        return Cpu(notes=[f"cpu: spec table says {from_field}, title says {from_title}; not grouped"])
    notes = []

    # Family and tier from the most specific text that has them.
    line = tier = None
    pro = False
    for t in (field_t, title_t, series_t):
        l, ti, p = _cpu_name_parts(vendor, t) if t else (None, None, False)
        pro = pro or p
        if line is None and l:
            line, tier = l, ti
        elif line == l and tier is None and ti:
            tier = ti
    if vendor == "intel" and re.match(r"[NU]\d", number):
        line, tier = ("Celeron" if line == "Celeron" else "Processor"), None

    hx = "HX" if vendor == "amd" and re.search(r"\bHX\s*-?\s*\d{3}\b", f"{field_t} {title_t}") else None
    words = [_VENDOR_LABEL[vendor]] + [w for w in (line, tier, "PRO" if pro else None, hx) if w] + [number]
    name = re.sub(r"\bCore i(\d) ", r"Core i\1-", " ".join(words))
    model, cls = f"{vendor}-{number.lower()}", cpu_class(vendor, number, line, tier)

    if vendor == "nvidia":
        # The N1X is sold with 18 and with 20 cores under one model number, at different
        # prices: the core count is part of the identity. Read from the raw text, because
        # _prep_cpu removes core counts.
        counts = {m.group(1) for m in re.finditer(r"(?i)\b(\d{1,2})\s*-?\s*cores?\b", f"{cores or ''} | {segment}")}
        if len(counts) != 1:
            return Cpu(name=name, cls=cls, notes=notes + ["cpu: core count missing or contradictory"])
        n = counts.pop()
        model, name = f"{model}-{n}c", f"{name} {n}-Core"
    return Cpu(model=model, name=name, cls=cls, notes=notes)


# --------------------------------------------------------------------------- GPU
_DISCRETE_GPUS = (
    (re.compile(r"\bRTX\s*PRO\s*(\d{3,4})\b", re.I), lambda m: f"nvidia-rtx-pro-{m.group(1)}"),
    (re.compile(r"\bRTX\s*(\d{3,4})\s*Ada\b", re.I), lambda m: f"nvidia-rtx-{m.group(1)}-ada"),
    (re.compile(r"\b(RTX|GTX)\s*-?\s*(\d{4})\s*-?\s*(TI|SUPER)?\b", re.I),
     lambda m: f"nvidia-{m.group(1).lower()}-{m.group(2)}" + (f"-{m.group(3).lower()}" if m.group(3) else "")),
    (re.compile(r"\bRTX\s*(A\d{3,4})\b", re.I), lambda m: f"nvidia-rtx-{m.group(1).lower()}"),
    (re.compile(r"\bGeForce\s*MX\s*(\d{3})\b", re.I), lambda m: f"nvidia-mx-{m.group(1)}"),
    (re.compile(r"\bQuadro\s*([A-Z]?\d{3,4})\b", re.I), lambda m: f"nvidia-quadro-{m.group(1).lower()}"),
    (re.compile(r"\bRadeon\s*(?:PRO\s*)?RX\s*(\d{4})\s*([MS])?\s*(XT)?\b", re.I),
     lambda m: f"amd-rx-{m.group(1)}" + (m.group(2) or "").lower() + (f"-{m.group(3).lower()}" if m.group(3) else "")),
)
# Intel Arc A-series is discrete only when the listing says so (Arc B390 is integrated).
_ARC_DISCRETE = re.compile(r"\bArc\s*(A\d{3})M?\b", re.I)


# Integrated graphics families. A listing that calls one of these "Dedicated" is a
# data-entry error (seen on several Dell listings), not a discrete GPU.
_INTEGRATED_GPU = re.compile(
    r"(?i)^\s*(intel\s+((uhd|hd|iris(\s+xe)?)\s+)?graphics(\s+\d+)?"
    r"|intel\s+arc(\s+graphics)?(\s+(\d{3}[VT]|B\d{3}))?"
    r"|amd\s+radeon(\s+graphics)?(\s+(vega\s+\d{1,2}|\d{3}M|8\d{3}S))?"   # 840M, 8060S; not RX 6600M
    r"|(qualcomm|snapdragon).*adreno.*|.*\badreno\b.*"
    r"|arm\s+(mali|immortalis).*|mediatek.*|apple.*"
    r"|nvidia\s+blackwell\s+rtx)\s*'?\s*$")


def _discrete_gpu(text: str | None) -> str | None:
    if not text:
        return None
    for pattern, build in _DISCRETE_GPUS:
        m = pattern.search(text)
        if m:
            return build(m)
    return None


def parse_gpu(graphics_type: str | None, graphics: str | None, title: str | None) -> tuple[str | None, str | None, list[str]]:
    """-> (gpu key value, display name, notes). Integrated graphics collapse to 'integrated'."""
    notes: list[str] = []
    graphics = _clean_text(graphics).rstrip("'") or None       # trademark signs sit inside "RTX(TM) 5070"
    name = graphics
    kind = (graphics_type or "").lower()
    dedicated = "dedicated" in kind or "discrete" in kind
    spec_gpu = _discrete_gpu(graphics)
    title_gpu = _discrete_gpu(_clean_text(title))

    if not spec_gpu and dedicated and graphics:
        m = _ARC_DISCRETE.search(graphics)
        if m:
            spec_gpu = f"intel-arc-{m.group(1).lower()}"
    if spec_gpu and title_gpu and spec_gpu != title_gpu:
        # A listing that names two different graphics cards is not grouped on either.
        return None, name, [f"gpu: spec table says {spec_gpu}, title says {title_gpu}; not grouped"]
    gpu = spec_gpu or title_gpu
    if gpu:
        if not spec_gpu:
            notes.append(f"gpu: {gpu} taken from the title (spec table lists {graphics!r})")
            name = gpu_label(gpu)
        return gpu, name, notes
    if name and _INTEGRATED_GPU.match(name):
        if dedicated:
            notes.append(f"gpu: {name!r} is integrated although the spec table says dedicated")
        return "integrated", name, notes
    if dedicated:
        return None, name, [f"gpu: listed as dedicated but no model recognised ({graphics!r})"]
    if "integrated" in kind:
        return "integrated", name, notes
    return None, name, [f"gpu: not recognised ({graphics!r}, type {graphics_type!r})"]


# --------------------------------------------------------------------------- capacities
def _gigabytes(value: str | None) -> float | None:
    """'512 gigabytes', '1 terabyte', '1TB', '512GB' -> GB as a number."""
    if not value:
        return None
    value = re.sub(r"(?<=\d),(?=\d{3}\b)", "", value)          # "1,024 gigabytes"
    m = re.search(r"(\d+(?:\.\d+)?)\s*(terabytes?|tb|gigabytes?|gb|megabytes?|mb)\b", value.lower())
    if not m:
        return None
    n = float(m.group(1))
    unit = m.group(2)
    if unit.startswith(("terabyte", "tb")):
        return n * 1024
    if unit.startswith(("megabyte", "mb")):
        return n / 1024
    return n


def snap_storage(gb: float | None) -> int | None:
    """Fold decimal and binary listings of the same drive together: 1000 and 1024 -> 1024."""
    if gb is None or gb <= 0:
        return None
    for size in _STORAGE_SIZES:
        if abs(gb - size) / size <= 0.08:
            return size
    return int(round(gb))


# A size written straight after a graphics card ("RTX 3050 6GB") is that card's memory;
# after a separator ("RTX 5060 - 32GB Memory") it is the system's.
_VIDEO_MEMORY = re.compile(r"(?i)\b(RTX|GTX|GeForce|Radeon)\s*(RX\s*)?[A-Z]?\d{3,4}\s*(Ti|SUPER)?\s+$")


def _title_memory(title: str) -> int | None:
    """System memory named in a title. "RTX 3050 6GB Memory" is the graphics card's."""
    for pattern in (r"(?i)(\d{1,3})\s?GB\s*(?:of\s+)?(?:LP)?(?:DDR\w*\s*)?(?:Memory|RAM)\b",
                    r"(?i)(\d{1,3})\s?GB\s+(?:LP)?DDR"):
        for m in re.finditer(pattern, title):
            if not _VIDEO_MEMORY.search(title[:m.start()]):
                return int(m.group(1))
    return None


def _title_storage(title: str) -> tuple[int | None, str | None]:
    """Storage named in a title, or nothing when the title lists two drives or an
    add-on ("512GB SSD + 1TB Dock", "1TB HDD + 256GB SSD"): one of a pair is not the total."""
    if re.search(r"(?i)\d\s?(?:GB|TB)[^,|-]{0,24}\+\s*\d+(?:\.\d+)?\s?(?:GB|TB)", title):
        return None, None
    m = re.search(r"(?i)(\d+(?:\.\d+)?)\s?(TB|GB)\s*(?:PCIe\s*(?:\d\.\d\s*)?|NVMe\s*|M\.2\s*|Gen\s?\d\s*)*"
                  r"(SSD|Solid State Drive|Storage|eMMC|UFS|HDD|Hard Drive)", title)
    if not m:
        return None, None
    gb = float(m.group(1)) * (1024 if m.group(2).upper() == "TB" else 1)
    kind = m.group(3).lower()
    stype = {"ssd": "ssd", "solid state drive": "ssd", "emmc": "emmc", "ufs": "ufs",
             "hdd": "hdd", "hard drive": "hdd"}.get(kind)
    return snap_storage(gb), stype


def parse_storage_type(value: str | None) -> str | None:
    if not value:
        return None
    kinds = set()
    for part in re.split(r"[,/+&]| and ", value.lower()):
        p = part.strip()
        if not p:
            continue
        if "emmc" in p:
            kinds.add("emmc")
        elif "ufs" in p:
            kinds.add("ufs")
        elif "ssd" in p or "solid state" in p or "nvme" in p or "flash" in p:
            kinds.add("ssd")
        elif "hdd" in p or "hard" in p:
            kinds.add("hdd")
        else:
            return None
    if len(kinds) == 1:
        return kinds.pop()
    return "hybrid" if kinds else None


# --------------------------------------------------------------------------- the rest
def parse_os(value: str | None, title: str | None) -> tuple[str | None, str | None]:
    text = (value or "").lower()
    if not text:
        t = (title or "").lower()
        if "chromebook" in t:
            return "chromeos", None
        if "macbook" in t:
            return "macos", None
        if "googlebook" in t:
            return "googlebook", None
        return None, None
    if "windows" in text:
        edition = "s" if "s mode" in text else "pro" if "pro" in text else "home" if "home" in text else None
        return "windows", edition
    if "googlebook" in text:
        return "googlebook", None
    if "chrome" in text:
        return "chromeos", None
    if "mac" in text:
        return "macos", None
    if "android" in text:
        return "android", None
    if "freedos" in text or "no os" in text or "without" in text:
        return "none", None
    if "linux" in text or "ubuntu" in text:
        return "linux", None
    return None, None


_DUAL_SCREEN = re.compile(r"(?i)\bdual[\s-]*(screen|display)\b|\bzenbook\s+duo\b|\byoga\s+book\b")
_TWO_IN_ONE = re.compile(r"(?i)\b(2|two)[\s-]*in[\s-]*(1|one)\b|\bx360\b|\bbook\d*(\s+pro)?\s+360\b|\bflip\b|\bspin\b"
                         r"|\bflex\s*\d|\bconvertible\b|\bthinkpad\b.*\byoga\b")
_DETACHABLE = re.compile(r"(?i)\bdetachable\b|\bsurface pro\b|\bflow z\d+\b|\bPZ1\d\b|\bduet\b|\btablet\b")
_TOUCH = re.compile(r"(?i)(?<!non[\s-])(?<!non)\btouch[\s-]*(screen)?\b")
# No laptop of 10 inches or more with its keyboard attached weighs under two pounds:
# a "2-in-1" that light is a tablet with a detachable keyboard.
DETACHABLE_MAX_LB = 2.0


def parse_weight_lb(value: str | None) -> float | None:
    """'1.41 pounds', '24 ounces' -> pounds."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(pounds?|lbs?|ounces?|oz)\b", (value or "").lower())
    if not m:
        return None
    return float(m.group(1)) / (16 if m.group(2).startswith("o") else 1)


def parse_form_factor(two_in_one: str | None, title: str | None, weight_lb: float | None = None) -> str:
    """dual-screen, then detachable terms, then convertible terms, else clamshell.
    'Yoga' alone is not a 2-in-1 signal: Yoga Slim and Yoga Pro are clamshells."""
    t = title or ""
    if _DUAL_SCREEN.search(t):
        return "dual-screen"
    if _DETACHABLE.search(t):
        return "detachable"
    if _bool(two_in_one) or _TWO_IN_ONE.search(t):
        return "detachable" if weight_lb is not None and weight_lb < DETACHABLE_MAX_LB else "convertible"
    return "clamshell"


def parse_touch(touch: str | None, title: str | None, form_factor: str) -> tuple[bool | None, list[str]]:
    spec = _bool(touch)
    in_title = bool(_TOUCH.search(title or ""))
    if spec is False and in_title:
        return True, ["touch: spec table says no, title says touchscreen; kept the title"]
    if spec is not None:
        return spec, []
    if in_title or form_factor != "clamshell":
        return True, []
    return None, ["touch: not stated"]


def parse_screen_size(value: str | None, title: str | None) -> float | None:
    for text in (value, title):
        if not text:
            continue
        m = re.search(r"(\d{1,2}(?:\.\d{1,2})?)\s*(?:inch(?:es)?\b|-inch\b|\"|”|″|''|in\.)", text, re.I)
        if m and 7 <= float(m.group(1)) <= 21:
            return float(m.group(1))
    return None


def parse_resolution(value: str | None) -> str | None:
    if not value:
        return None
    m = re.search(r"(\d{3,4})\s*[x×]\s*(\d{3,4})", value.lower())
    if not m:
        return None
    res = f"{m.group(1)}x{m.group(2)}"
    return _RESOLUTION_FIXES.get(res, res)


# Generic words around a model line. "Laptop" and "Gaming" are dropped only where they
# are generic: "Surface Laptop" and "TUF Gaming A16" are product names.
_FAMILY_NOISE = re.compile(r"(?i)\b(copilot\s*\+?\s*pc|ai\s*pc|latest model|gaming laptop|business laptop|notebook|"
                           r"touch[\s-]*screen|(?<!surface )laptop|new|geek squad certified( refurbished)?)\b")
# Where a title without an inch mark stops naming the model and starts listing specs.
_SPEC_CUT = re.compile(r"(?i)\s(1[0-8]\.\d\b|\d{3,4}\s?x\s?\d{3,4}|WUXGA\b|WQXGA\b|FHD\b|QHD\b|UHD\b|OLED\b|\d+\s?Hz\b"
                       r"|(U[579]|i[3579]|R[3579])\b|N\d{2,3}\b|\d+\s?(G|GB|T|TB)\b)")
_SUB_BRAND = {"hp omen": "OMEN", "alienware": "Alienware"}


def parse_model_family(title: str | None, brand: str | None) -> str | None:
    """Best-effort model line from the title: the words between the brand and the screen size."""
    t = _clean_text(title)
    if not t:
        return None
    t = re.sub(r"^\s*" + re.escape(brand) + r"\s*-\s*", "", t, flags=re.I) if brand else t
    cut = re.search(r"\b\d{1,2}(?:\.\d{1,2})?\s*(?:\"|”|″|''|-inch\b|inch\b|�)", t)
    if cut:
        head = t[:cut.start()]
    else:
        head = t.split(" - ")[0]
        spec = _SPEC_CUT.search(head)
        head = head[:spec.start()] if spec else head
    head = _FAMILY_NOISE.sub(" ", head)
    head = re.sub(r"\s+-\s*|\s*-\s+", " ", head)      # separators only: "2-in-1" keeps its hyphens
    head = re.sub(r"\s+", " ", head).strip(" -,")
    if not head or re.fullmatch(r"[\d.]+", head) or len(head) > 60:
        return None
    sub = _SUB_BRAND.get((brand or "").strip().lower())     # "HP OMEN - MAX 16" is the OMEN MAX
    if sub and not head.lower().startswith(sub.lower()):
        head = f"{sub} {head}"
    return head


# --------------------------------------------------------------------------- entry point
def normalise(product: dict) -> Specs:
    """Normalise one collected product (dict with sku, title, brand, raw_specs)."""
    raw = product.get("raw_specs") or {}
    title = _clean_text(product.get("title"))
    s = Specs(sku=str(product["sku"]))
    notes = s.review_notes

    listed_brand = product.get("brand") or _lookup(raw, "Brand", ("General",))
    s.brand = canonical_brand(listed_brand)
    s.model_family = parse_model_family(title, listed_brand)
    s.model_number = _lookup(raw, "Model Number", ("General",))
    color = (_lookup(raw, "Color", ("General",)) or "").split(",")[0].strip()      # "Magnetite, Other"
    s.color = color or None

    # CPU
    cpu = parse_cpu(
        _lookup(raw, "Processor Brand", ("Processor",)),
        _lookup(raw, "Processor Model", ("Processor", "Key Specs")),
        _lookup(raw, "Processor Model Number", ("Processor",)),
        title,
        _lookup(raw, "Number of CPU Cores", ("Processor",)))
    s.cpu_model, s.cpu_name, s.cpu_class = cpu.model, cpu.name, cpu.cls
    notes += cpu.notes

    # Memory
    mem = _gigabytes(_lookup(raw, "System Memory (RAM)", ("Memory", "Key Specs")))
    title_mem = _title_memory(title)
    if mem is None and title_mem:
        mem = float(title_mem)
    if mem is not None and 1 <= mem <= 512:
        s.memory_gb = int(round(mem))
        if title_mem and title_mem != s.memory_gb:
            notes.append(f"memory: spec table says {s.memory_gb} GB, title says {title_mem} GB; kept the spec table")
    else:
        notes.append("memory: not parsed")

    # Storage
    title_storage, title_stype = _title_storage(title)
    s.storage_gb = snap_storage(_gigabytes(_lookup(raw, "Total Storage Capacity", ("Storage", "Key Specs"))))
    if s.storage_gb is None:
        s.storage_gb = title_storage
    if s.storage_gb is None:
        notes.append("storage: capacity not parsed")
    elif title_storage and title_storage != s.storage_gb:
        notes.append(f"storage: spec table says {s.storage_gb} GB, title says {title_storage} GB; kept the spec table")
    s.storage_type = parse_storage_type(_lookup(raw, "Storage Type", ("Storage", "Key Specs"))) or title_stype
    if s.storage_type is None:
        notes.append("storage: type not parsed")

    # Chassis
    s.form_factor = parse_form_factor(_lookup(raw, "2-in-1 Design", ("Features", "Key Specs")), title,
                                      parse_weight_lb(_lookup(raw, "Product Weight", ("Dimensions",))))
    s.touchscreen, n = parse_touch(_lookup(raw, "Touch Screen", ("Display", "Key Specs")), title, s.form_factor)
    notes += n

    # OS
    s.os, s.os_edition = parse_os(_lookup(raw, "Operating System", ("Compatibility",)), title)
    if s.os is None:
        notes.append("os: not parsed")

    # GPU
    s.gpu, s.gpu_name, n = parse_gpu(_lookup(raw, "Graphics Type", ("Graphics",)),
                                     _lookup(raw, "Graphics", ("Graphics", "Key Specs")), title)
    notes += n

    # Screen: compared at the strict level only (by bucket); the resolution is reported, never compared
    s.screen_size_in = parse_screen_size(_lookup(raw, "Screen Size", ("Display", "Key Specs")), title)
    s.screen_bucket = screen_bucket(s.screen_size_in)
    s.resolution = parse_resolution(_lookup(raw, "Screen Resolution", ("Display", "Key Specs")))

    assign_keys(s)
    return s


# --------------------------------------------------------------------------- labels
def _capacity(gb: int) -> str:
    if gb >= 1024 and gb % 1024 == 0:
        return f"{gb // 1024} TB"
    return f"{gb} GB"


_OS_LABEL = {"windows": "Windows", "chromeos": "ChromeOS", "macos": "macOS", "googlebook": "Googlebook OS",
             "linux": "Linux", "android": "Android", "none": "No OS"}


def gpu_label(gpu: str) -> str:
    """nvidia-rtx-5070-ti -> RTX 5070 Ti"""
    out = []
    for w in gpu.split("-")[1:]:
        if w in ("ti", "super", "arc", "quadro"):
            out.append(w.capitalize())
        else:
            out.append(w.upper())
    return " ".join(out) or gpu


def group_label(spec: dict, cpu_name: str | None = None, level: str = "exact") -> str:
    """'Core Ultra 5 226V · 16 GB · 512 GB SSD · Convertible touch · Windows'

    Relaxed and class labels leave out what those levels ignore (storage type,
    touchscreen); a class label names the processor class instead of the model.
    A strict label adds the screen size bucket: '... · Convertible touch · 15–16" · Windows'."""
    name = cpu_name or spec.get("cpu_name") or spec.get("cpu_model") or "?"
    if level == "class":
        name = cpu_class_label(spec.get("cpu_class") or "") or name
    name = re.sub(r"^Intel (?=Core|Processor|Celeron|Pentium)", "", name)
    storage = _capacity(spec["storage_gb"])
    chassis = spec["form_factor"].capitalize()
    if level in ("strict", "exact"):
        storage += " " + {"ssd": "SSD", "emmc": "eMMC", "ufs": "UFS", "hdd": "HDD", "hybrid": "SSD+HDD"}.get(
            spec["storage_type"], spec["storage_type"])
        chassis += " touch" if spec["touchscreen"] else " non-touch"
    parts = [name, f"{spec['memory_gb']} GB", storage, chassis, _OS_LABEL.get(spec["os"], spec["os"])]
    if level == "strict":
        parts.insert(-1, f'{spec["screen_bucket"]}"')
    if spec.get("gpu") and spec["gpu"] != "integrated":
        parts.insert(1, gpu_label(spec["gpu"]))
    return " · ".join(parts)
