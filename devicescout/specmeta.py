"""Human-facing metadata the UI renders from: spec labels/units, use cases, must-have options.

Kept in Python (not duplicated in the frontend) so the CLI, API and UI never disagree.
"""

from __future__ import annotations

from .models import Category
from .scoring import PROFILES

C = Category

CATEGORY_LABELS = {
    C.PHONE: "Phones", C.LAPTOP: "Laptops", C.TABLET: "Tablets", C.SMARTWATCH: "Smartwatches",
    C.POWER_BANK: "Power banks", C.CHARGER: "Chargers", C.EARBUDS: "Earbuds", C.CASE: "Cases",
    C.CABLE: "Cables", C.ACCESSORY: "Accessories",
}

# key -> (label, unit, group)
SPECS: dict[str, tuple[str, str, str]] = {
    "os": ("Operating system", "", "Platform"),
    "chipset": ("Processor", "", "Platform"),
    "gpu": ("Graphics", "", "Platform"),
    "has_dedicated_gpu": ("Dedicated graphics", "bool", "Platform"),
    "ram_gb": ("RAM", "GB", "Memory"),
    "storage_gb": ("Storage", "GB", "Memory"),
    "display_size_in": ("Screen size", "in", "Display"),
    "refresh_rate_hz": ("Refresh rate", "Hz", "Display"),
    "resolution_px": ("Resolution", "px", "Display"),
    "main_camera_mp": ("Main camera", "MP", "Camera"),
    "camera_count": ("Rear cameras", "", "Camera"),
    "optical_zoom_x": ("Optical zoom", "x", "Camera"),
    "has_ois": ("Optical stabilisation", "bool", "Camera"),
    "battery_mah": ("Battery", "mAh", "Battery"),
    "battery_wh": ("Battery", "Wh", "Battery"),
    "charging_w": ("Charging", "W", "Battery"),
    "capacity_mah": ("Capacity", "mAh", "Battery"),
    "output_w": ("Max output", "W", "Battery"),
    "weight_g": ("Weight", "g", "Build"),
    "water_resistance": ("Water resistance", "", "Build"),
    "has_5g": ("5G", "bool", "Connectivity"),
    "has_nfc": ("NFC", "bool", "Connectivity"),
    "has_gps": ("GPS", "bool", "Connectivity"),
    "ports": ("Ports", "", "Connectivity"),
    "release_year": ("Released", "", "Other"),
    "expert_score": ("Expert review score", "/100", "Reviews"),
    "benchmark_score": ("Benchmark", "", "Reviews"),
}

USES: dict[str, tuple[str, str]] = {
    "balanced": ("All-rounder", "A bit of everything, no big weaknesses"),
    "everyday": ("Everyday use", "Social media, calls, video, a full day on one charge"),
    "photography": ("Photography", "Sharp photos, zoom, stabilisation, reviewed camera quality"),
    "gaming": ("Gaming", "Fast processor, smooth screen, stays charged"),
    "battery": ("Battery life", "Lasts longest between charges"),
    "fast_charging": ("Fast charging", "Tops up quickly"),
    "portability": ("Light & compact", "Easy to carry"),
    "display": ("Best screen", "Sharp, smooth, big display"),
    "student": ("Student", "Long battery, light, enough power for study"),
    "business": ("Work & business", "Reliable, long-lasting, recent model"),
    "programming": ("Programming", "Lots of RAM, strong processor"),
    "content_creation": ("Content creation", "Video editing, design, storage"),
    "fitness": ("Fitness", "GPS, water resistance, battery"),
}

# Must-have filters offered per category. op applies as `spec <op> value`.
MUSTS: list[dict] = [
    {"key": "has_5g", "label": "5G", "type": "bool", "categories": ["phone", "tablet"]},
    {"key": "has_nfc", "label": "NFC (tap to pay)", "type": "bool", "categories": ["phone", "smartwatch"]},
    {"key": "has_ois", "label": "Optical stabilisation", "type": "bool", "categories": ["phone"]},
    {"key": "has_gps", "label": "Built-in GPS", "type": "bool", "categories": ["smartwatch"]},
    {"key": "water_rating", "label": "Water resistant (IP67+)", "type": "min", "value": 7,
     "categories": ["phone", "smartwatch"]},
    {"key": "has_dedicated_gpu", "label": "Dedicated graphics card", "type": "bool", "categories": ["laptop"]},
    {"key": "ram_gb", "label": "Minimum RAM", "type": "min", "unit": "GB",
     "options": [4, 6, 8, 12, 16, 32], "categories": ["phone", "laptop", "tablet"]},
    {"key": "storage_gb", "label": "Minimum storage", "type": "min", "unit": "GB",
     "options": [64, 128, 256, 512, 1024], "categories": ["phone", "laptop", "tablet"]},
    {"key": "battery_mah", "label": "Minimum battery", "type": "min", "unit": "mAh",
     "options": [4000, 5000, 6000], "categories": ["phone"]},
    {"key": "charging_w", "label": "Minimum charging speed", "type": "min", "unit": "W",
     "options": [25, 45, 65, 100], "categories": ["phone"]},
    {"key": "refresh_rate_hz", "label": "Minimum refresh rate", "type": "min", "unit": "Hz",
     "options": [90, 120, 144, 165], "categories": ["phone", "laptop", "tablet"]},
    {"key": "weight_g", "label": "Maximum weight", "type": "max", "unit": "g",
     "options": [170, 190, 210], "categories": ["phone"]},
    {"key": "weight_g", "label": "Maximum weight", "type": "max", "unit": "g",
     "options": [1300, 1600, 2000], "categories": ["laptop"]},
    {"key": "display_size_in", "label": "Maximum screen size", "type": "max", "unit": "in",
     "options": [6.1, 6.4, 6.7], "categories": ["phone"]},
    {"key": "display_size_in", "label": "Maximum screen size", "type": "max", "unit": "in",
     "options": [13.5, 14, 15.6], "categories": ["laptop"]},
    {"key": "capacity_mah", "label": "Minimum capacity", "type": "min", "unit": "mAh",
     "options": [10000, 20000, 25000], "categories": ["power_bank"]},
    {"key": "output_w", "label": "Minimum output", "type": "min", "unit": "W",
     "options": [20, 45, 65, 100], "categories": ["power_bank", "charger"]},
]

OS_OPTIONS = {
    "phone": ["android", "ios"], "tablet": ["android", "ipados"],
    "laptop": ["windows", "macos", "chromeos", "linux"], "smartwatch": ["wearos", "watchos"],
}

# Budget presets in NPR per category (for the slider/quick picks).
BUDGETS = {
    "phone": [15000, 25000, 40000, 60000, 100000, 200000],
    "laptop": [50000, 80000, 120000, 180000, 300000],
    "tablet": [25000, 50000, 90000, 150000],
    "smartwatch": [5000, 15000, 30000, 60000],
    "power_bank": [2000, 4000, 7000, 12000],
    "earbuds": [2000, 5000, 12000, 30000],
    "charger": [1000, 2500, 5000],
}


def meta() -> dict:
    cats = []
    for c, label in CATEGORY_LABELS.items():
        uses = [u for u in USES if c in PROFILES.get(u, {})]
        cats.append({
            "id": c.value, "label": label,
            "uses": [{"id": u, "label": USES[u][0], "description": USES[u][1]} for u in uses],
            "musts": [m for m in MUSTS if c.value in m["categories"]],
            "os": OS_OPTIONS.get(c.value, []),
            "budgets": BUDGETS.get(c.value, [5000, 20000, 50000, 100000]),
        })
    return {
        "categories": cats,
        "specs": {k: {"label": l, "unit": u, "group": g} for k, (l, u, g) in SPECS.items()},
    }
