"""Turn messy, source-specific spec strings into the canonical SPEC_KEYS vocabulary.

Every source labels things differently ("Battery", "Akku", "batdescription1",
"Battery capacity") and formats values differently ("5000 mAh", "5,000mAh",
"Li-Ion 5000 mAh, non-removable"). This module is where that chaos is absorbed.
"""

from __future__ import annotations

import re
from typing import Any

from .models import Category, Product

_NUM = r"(\d+(?:[.,]\d+)?)"


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def _first(pattern: str, text: str, flags: int = re.I) -> float | None:
    m = re.search(pattern, text, flags)
    return _num(m.group(1)) if m else None


def parse_price(value) -> float | None:
    """'Rs. 1,49,999' (Nepali/Indian lakh grouping), '1,299.00', '1.349,00 €', 1999, '1.5 lakh'."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) or None
    text = str(value).strip().lower()
    if (m := re.search(r"(\d+(?:\.\d+)?)\s*(lakh|lac)", text)):
        return float(m.group(1)) * 100_000
    # First numeric token only: "Rs. 24,999 - Rs. 27,999" -> 24999
    m = re.search(r"\d[\d.,]*", text)
    if not m:
        return None
    s = m.group(0).rstrip(".,")
    if "," in s and "." in s:
        # whichever separator comes last is the decimal point
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".") if len(s.split(",")[-1]) == 2 else s.replace(",", "")
    elif s.count(".") > 1 or (s.count(".") == 1 and len(s.split(".")[-1]) == 3):
        s = s.replace(".", "")  # "1.349" as EU thousands
    try:
        v = float(s)
    except ValueError:
        return None
    return v or None


def parse_variant(text: str) -> str | None:
    """'Redmi Note 14 (8GB/256GB)' -> '8/256'; '12GB RAM 512GB' -> '12/512'; '256GB' -> '256'."""
    for pattern in (
        r"(\d{1,2})\s*(?:GB)?\s*(?:RAM)?\s*[/+|]\s*(\d{2,4}|1)\s*(GB|TB)",   # 8GB/256GB, 8+256GB
        r"(\d{1,2})\s*GB\s*(?:RAM)?[\s,]{0,6}(\d{2,4}|1)\s*(GB|TB)",          # 12GB RAM 512GB, 8GB 256GB
        r"\b(\d{1,2})\s*/\s*(32|64|128|256|512|1)()\b",                         # 8/256 (no unit)
    ):
        m = re.search(pattern, text, re.I)
        if m and int(m.group(1)) <= 24:
            tb = m.group(3).upper() == "TB" or (m.group(2) == "1" and not m.group(3))
            return f"{m.group(1)}/{m.group(2)}{'TB' if tb else ''}"
    m = re.search(r"\b(\d{2,4}|1)\s*(GB|TB)\b", text, re.I)
    if m:
        return f"{m.group(1)}{'TB' if m.group(2).upper() == 'TB' else ''}"
    return None


def parse_label_lines(text: str) -> dict[str, str]:
    """Spec lists written as prose in product descriptions, very common on Nepali stores:

    'Display: 6.67" AMOLED 120Hz\\nRAM : 8GB\\n• Battery - 5500mAh'
    """
    out: dict[str, str] = {}
    for line in re.split(r"[\n\r•·▪●]+", text):
        m = re.match(r"\s*[-*]?\s*([A-Za-z][A-Za-z /&()+.]{1,40}?)\s*[:：–-]\s+(.+)", line)
        if m:
            label, value = m.group(1).strip(), m.group(2).strip()
            if len(value) <= 300:
                out.setdefault(label, value)
    return out


def parse_capacity_gb(text: str) -> float | None:
    m = re.search(_NUM + r"\s*(TB|GB)", text, re.I)
    if not m:
        return None
    val = _num(m.group(1))
    return val * 1024 if m.group(2).upper() == "TB" else val


def parse_ram_storage(text: str) -> tuple[float | None, float | None]:
    """GSMArena style: '256GB 12GB RAM, 512GB 12GB RAM'. Takes the max of each."""
    ram = [_num(x) for x in re.findall(_NUM + r"\s*GB\s*RAM", text, re.I)]
    storage: list[float] = []
    for val, unit in re.findall(_NUM + r"\s*(TB|GB)(?!\s*RAM)", text, re.I):
        v = _num(val) * (1024 if unit.upper() == "TB" else 1)
        storage.append(v)
    return (max(ram) if ram else None, max(storage) if storage else None)


def parse_os(text: str) -> str | None:
    t = text.lower()
    for needle, os_name in (
        ("wear os", "wearos"), ("watchos", "watchos"), ("ipados", "ipados"),
        ("ios", "ios"), ("android", "android"), ("harmonyos", "harmonyos"),
        ("chrome", "chromeos"), ("macos", "macos"), ("mac os", "macos"),
        ("windows", "windows"), ("linux", "linux"), ("tizen", "tizen"),
    ):
        if re.search(rf"\b{re.escape(needle)}", t):
            return os_name
    return None


def parse_resolution(text: str) -> int | None:
    m = re.search(r"(\d{3,4})\s*[x×]\s*(\d{3,4})", text)
    return int(m.group(1)) * int(m.group(2)) if m else None


def parse_camera(text: str) -> dict[str, Any]:
    """'200 MP, f/1.7 ... OIS\n50 MP ... 5x optical zoom\n12 MP ultrawide'."""
    mps = [_num(x) for x in re.findall(_NUM + r"\s*MP", text, re.I)]
    zooms = [_num(x) for x in re.findall(_NUM + r"\s*x\s*optical", text, re.I)]
    out: dict[str, Any] = {}
    if mps:
        out["main_camera_mp"] = max(mps)
        out["camera_count"] = len(mps)
    if zooms:
        out["optical_zoom_x"] = max(zooms)
    out["has_ois"] = bool(re.search(r"\bOIS\b", text))
    return out


# Label aliases per canonical key. Lower-cased substring match on the raw label.
_LABELS: dict[str, tuple[str, ...]] = {
    "network": ("technology", "network", "connectivity", "cellular"),
    "nfc": ("nfc",),
    "gps": ("gps", "positioning", "navigation"),
    "os": ("os", "operating system", "platform"),
    "chipset": ("chipset", "processor", "cpu", "soc"),
    "gpu": ("gpu", "graphics", "graphic card", "video card"),
    "memory": ("internal", "memory", "ram", "storage", "ssd", "hard drive"),
    "display_size": ("size", "screen size", "display size", "display"),
    "refresh": ("refresh",),
    "resolution": ("resolution",),
    "battery": ("battery", "batdescription", "capacity"),
    "charging": ("charging", "fast charge", "output", "power delivery", "wattage"),
    "camera": ("main camera", "rear camera", "triple", "quad", "dual", "single", "camera"),
    "weight": ("weight", "weighs"),
    "water": ("ip rating", "water", "protection", "ingress", "durability", "body"),
    "ports": ("ports", "usb", "connectors", "interfaces"),
    "year": ("announced", "release", "launched", "status"),
}


def _match(label: str, key: str) -> bool:
    # Word-boundary match so "os" hits "Platform / OS" but not "Positioning".
    lab = label.lower()
    return any(re.search(rf"\b{re.escape(alias)}\b", lab) for alias in _LABELS[key])


def normalize_specs(raw: dict[str, str], category: Category) -> dict[str, Any]:
    specs: dict[str, Any] = {}
    blob = " \n".join(f"{k}: {v}" for k, v in raw.items())

    for label, value in raw.items():
        if not value:
            continue
        v = value.strip()
        # Connectivity flags can sit under any label ("Network / Technology", "Connectivity").
        is_conn = _match(label, "network") or _match(label, "nfc") or _match(label, "gps")
        if is_conn:
            if re.search(r"\b5G\b", v):
                specs["has_5g"] = True
            elif _match(label, "network") and re.search(r"\b(LTE|4G)\b", v):
                specs.setdefault("has_5g", False)
            if _match(label, "nfc"):
                specs["has_nfc"] = bool(re.match(r"\s*(yes|supported|available)", v, re.I))
            elif re.search(r"\bNFC\b", v):
                specs["has_nfc"] = True
            if _match(label, "gps"):
                specs["has_gps"] = not re.match(r"\s*(no|none)\b", v, re.I)
            elif re.search(r"\bGPS\b", v):
                specs["has_gps"] = True
        elif _match(label, "os") and "os" not in specs:
            if (os_name := parse_os(v)):
                specs["os"] = os_name
        elif _match(label, "chipset") and "chipset" not in specs:
            specs["chipset"] = v.split("\n")[0][:120]
        elif _match(label, "gpu") and "gpu" not in specs:
            specs["gpu"] = v[:120]
        elif _match(label, "refresh"):
            if (hz := _first(_NUM + r"\s*Hz", v)):
                specs["refresh_rate_hz"] = hz
        elif _match(label, "resolution"):
            if (px := parse_resolution(v)):
                specs["resolution_px"] = px
        elif _match(label, "memory"):
            ram, storage = parse_ram_storage(v)
            if "ram" in label.lower() and ram is None:
                ram = parse_capacity_gb(v)
                storage = None
            if ram:
                specs["ram_gb"] = max(ram, specs.get("ram_gb", 0))
            if storage and "ram" not in label.lower():
                specs["storage_gb"] = max(storage, specs.get("storage_gb", 0))
        elif (_match(label, "camera") and category in (Category.PHONE, Category.TABLET)
              and not re.search(r"selfie|front", label, re.I)):
            for k, val in parse_camera(v).items():
                if k not in specs or (isinstance(val, (int, float)) and val > specs[k]):
                    specs[k] = val
        elif _match(label, "weight"):
            if (g := _first(_NUM + r"\s*g\b", v)):
                specs["weight_g"] = g
            elif (kg := _first(_NUM + r"\s*kg", v)):
                specs["weight_g"] = kg * 1000
        elif _match(label, "water"):
            if (m := re.search(r"\b(IP[X\d]\d|\d+\s*ATM)\b", v, re.I)):
                specs["water_resistance"] = m.group(1).upper().replace(" ", "")
        elif _match(label, "ports"):
            specs["ports"] = v[:200]
        elif _match(label, "year"):
            if (m := re.search(r"\b(20\d{2})\b", v)):
                specs.setdefault("release_year", int(m.group(1)))
        elif _match(label, "display_size"):
            if (inch := _first(_NUM + r"\s*(?:inches|inch|in\b|\"|”)", v)):
                specs["display_size_in"] = inch
            if (px := parse_resolution(v)):
                specs.setdefault("resolution_px", px)
            if (hz := _first(_NUM + r"\s*Hz", v)):
                specs["refresh_rate_hz"] = max(hz, specs.get("refresh_rate_hz", 0))

        # These can appear under many different labels, so check independently.
        if _match(label, "battery") or _match(label, "charging"):
            if (mah := _first(_NUM + r"\s*mAh", v.replace(",", ""))):
                key = "capacity_mah" if category == Category.POWER_BANK else "battery_mah"
                specs[key] = max(mah, specs.get(key, 0))
            if (wh := _first(_NUM + r"\s*Wh\b", v)):
                specs["battery_wh"] = wh
            watts = [_num(x) for x in re.findall(_NUM + r"\s*W\b", v)]
            if watts:
                key = "output_w" if category in (Category.POWER_BANK, Category.CHARGER) else "charging_w"
                specs[key] = max(max(watts), specs.get(key, 0))

    # Laptops: infer dedicated GPU from well-known names.
    if category == Category.LAPTOP:
        gpu_text = specs.get("gpu", "") or blob
        specs["has_dedicated_gpu"] = bool(
            re.search(r"\b(RTX|GTX|Radeon RX|Arc A\d)", gpu_text, re.I)
        )
        if "os" not in specs and (os_name := parse_os(blob)):
            specs["os"] = os_name

    return specs


# --- Categorization -------------------------------------------------------

_CATEGORY_RULES: list[tuple[Category, tuple[str, ...]]] = [
    # Order matters: accessories first, because "iPhone 16 case" contains "iphone".
    (Category.POWER_BANK, ("power bank", "powerbank", "portable charger", "battery pack", "magsafe battery")),
    (Category.CASE, (" case", "cover", "screen protector", "tempered glass", "sleeve")),
    (Category.CABLE, ("cable", "cord", "lightning to", "usb-c to")),
    (Category.CHARGER, ("charger", "charging brick", "wall adapter", "gan ", "power adapter")),
    (Category.EARBUDS, ("earbuds", "airpods", "buds", "headphones", "earphones")),
    (Category.SMARTWATCH, ("watch", "smartwatch", "fitness tracker", "band ")),
    (Category.TABLET, ("ipad", "tablet", "galaxy tab", " pad ")),
    (Category.LAPTOP, ("laptop", "notebook", "macbook", "chromebook", "thinkpad", "zenbook",
                       "vivobook", "ideapad", "legion", "rog ", "xps", "spectre", "pavilion")),
    (Category.PHONE, ("phone", "iphone", "galaxy s", "galaxy a", "galaxy z", "pixel", "xiaomi",
                      "redmi", "oneplus", "poco", "oppo", "vivo", "realme", "motorola", "moto g",
                      "nothing phone", "honor")),
]


def categorize(name: str, hint: str | None = None) -> Category:
    """Classify by product name, falling back to a breadcrumb/category hint from the source."""
    for text in (f" {name.lower()} ", f" {(hint or '').lower()} "):
        for cat, needles in _CATEGORY_RULES:
            if any(n in text for n in needles):
                return cat
    return Category.UNKNOWN


# --- Identity / dedup ------------------------------------------------------

_NOISE = re.compile(
    r"\b(\d+\s*(gb|tb)(\s*ram)?|5g|4g|lte|wi-?fi|unlocked|dual sim|renewed|refurbished|"
    r"black|white|blue|green|red|gray|grey|silver|gold|titanium|graphite|midnight|"
    r"starlight|violet|purple|pink|cream|lavender|phantom|onyx|natural|desert|obsidian|porcelain|"
    r"hazel|peony|rose|mint|sage|charcoal|jade|amber|marble|space|ultramarine|teal|yellow|orange)\b",
    re.I,
)


# Marketplace title junk: 'Redmi 13 (8/256) - 1 Year Warranty | Free Gift', 'Brand New Sealed'.
_TITLE_TAIL = re.compile(r"\s+(?:[-–|]|with\b|\+\s*free\b)\s+.*$", re.I)
_TITLE_JUNK = re.compile(
    r"\b(\d+\s*(years?|yrs?|months?)\s*(official\s*)?warranty|official(ly)?|warranty|brand\s*new|"
    r"original|genuine|sealed|pack|nepal|price\s*in\s*nepal|emi|mobile\s*phone|smartphone|"
    r"smart\s*phone|\d{1,2}\s*/\s*\d{2,4}\s*(gb|tb)?|\d{1,2}\s*\+\s*\d{2,4}\s*(gb|tb)?)\b",
    re.I,
)


_PRICE_ARTICLE = re.compile(r"\s+(price\s+in\s+nepal|price\s*&\s*specs|full\s+specifications)\b.*$", re.I)


def clean_title(name: str) -> str:
    """Drop listing tails: ' - 1 Year Warranty', ' | Free Gift', ' Price in Nepal, Specs'."""
    name = _PRICE_ARTICLE.sub("", name)
    return re.sub(r"\s+", " ", _TITLE_TAIL.sub("", name)).strip()


def canonical_key(brand: str | None, name: str) -> str:
    """A key that collapses storage/colour/carrier/listing-noise variants of one model.

    'Samsung Galaxy S24 Ultra 5G 256GB Titanium Black', 'Galaxy S24 Ultra (12GB/512GB)' and
    'Samsung Galaxy S24 Ultra (12/256) - 1 Year Official Warranty' -> 'samsung galaxy s24 ultra'
    """
    n = clean_title(name).lower()
    b = (brand or "").lower().strip()
    if b in ("no brand", "generic", "oem"):
        b = ""
    n = _TITLE_JUNK.sub(" ", n)
    n = re.sub(r"[()\[\],/|+]", " ", n)
    n = re.sub(r"(?<=\s)-(?=\s)|^-|-$", " ", n)
    n = _NOISE.sub(" ", n)
    n = re.sub(r"\s+", " ", n).strip()
    if b and not n.startswith(b):
        n = f"{b} {n}"
    return n


def infer_os(category: Category, text: str) -> str | None:
    """Store listings rarely state the OS, but for most devices the model name settles it."""
    t = text.lower()
    if category == Category.PHONE:
        if "iphone" in t or re.match(r"\s*apple\b", t):
            return "ios"
        if "huawei" in t:
            return None  # HarmonyOS or Android depending on model/market
        return "android"
    if category == Category.TABLET:
        return "ipados" if ("ipad" in t or "apple" in t) else "android" if "huawei" not in t else None
    if category == Category.SMARTWATCH and "apple watch" in t:
        return "watchos"
    if category == Category.LAPTOP:
        if "macbook" in t:
            return "macos"
        if "chromebook" in t:
            return "chromeos"
    return None


def finalize(product: Product, category_hint: str | None = None) -> Product:
    if product.category == Category.UNKNOWN:
        product.category = categorize(product.name, category_hint)
    specs = normalize_specs(product.raw_specs, product.category)
    if product.category in (Category.PHONE, Category.TABLET) and re.search(r"\b5G\b", product.name):
        specs["has_5g"] = True
    if "os" not in specs and (os_name := infer_os(product.category, f"{product.brand or ''} {product.name}")):
        specs["os"] = os_name
    product.specs = {**specs, **product.specs}
    return product
