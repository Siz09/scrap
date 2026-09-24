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
        if _match(label, "os") and "os" not in specs:
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


def canonical_key(brand: str | None, name: str) -> str:
    """A key that collapses storage/colour/carrier variants of the same model.

    'Samsung Galaxy S24 Ultra 5G 256GB Titanium Black' and
    'Galaxy S24 Ultra (12GB/512GB)' -> 'samsung galaxy s24 ultra'
    """
    n = name.lower()
    b = (brand or "").lower().strip()
    n = re.sub(r"[()\[\],/|+-]", " ", n)
    n = _NOISE.sub(" ", n)
    n = re.sub(r"\s+", " ", n).strip()
    if b and not n.startswith(b):
        n = f"{b} {n}"
    return n


def finalize(product: Product, category_hint: str | None = None) -> Product:
    if product.category == Category.UNKNOWN:
        product.category = categorize(product.name, category_hint)
    product.specs = {**normalize_specs(product.raw_specs, product.category), **product.specs}
    return product
