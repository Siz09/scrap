"""Understand a buyer's request written in plain words.

  "photography phone under 1.2 lakh"
  "gaming laptop between 1 lakh and 1.5 lakh with rtx, no hp"
  "long lasting android phone around 50k with 5g and nfc"
  "20000mah power bank under 5 hajar"

Rule-based on purpose: works offline, is instant and free, and gives the same answer
every time. The result is shown back to the user as editable choices, so a
misunderstanding is visible and fixable rather than silently wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .models import Category

C = Category

# Longest phrases first so "battery life" wins over "battery", "long battery" over "long".
CATEGORY_WORDS: list[tuple[str, Category]] = [
    ("power bank", C.POWER_BANK), ("powerbank", C.POWER_BANK), ("portable charger", C.POWER_BANK),
    ("smartwatch", C.SMARTWATCH), ("smart watch", C.SMARTWATCH), ("fitness band", C.SMARTWATCH),
    ("watch", C.SMARTWATCH), ("band", C.SMARTWATCH),
    ("earbuds", C.EARBUDS), ("earphones", C.EARBUDS), ("earphone", C.EARBUDS), ("airpods", C.EARBUDS),
    ("buds", C.EARBUDS), ("tws", C.EARBUDS), ("headphones", C.EARBUDS),
    ("macbook", C.LAPTOP), ("chromebook", C.LAPTOP), ("notebook", C.LAPTOP), ("laptop", C.LAPTOP),
    ("ipad", C.TABLET), ("tablet", C.TABLET), ("tab", C.TABLET),
    ("charger", C.CHARGER), ("adapter", C.CHARGER),
    ("smartphone", C.PHONE), ("mobile", C.PHONE), ("phone", C.PHONE), ("iphone", C.PHONE), ("handset", C.PHONE),
]

USE_WORDS: list[tuple[str, str]] = [
    # battery before longevity: "long battery" is about battery
    ("long battery", "battery"), ("battery life", "battery"), ("battery backup", "battery"),
    ("big battery", "battery"), ("battery", "battery"), ("backup", "battery"), ("all day", "battery"),
    ("fast charging", "fast_charging"), ("quick charging", "fast_charging"), ("quick charge", "fast_charging"),
    ("charging speed", "fast_charging"),
    ("future proof", "longevity"), ("future-proof", "longevity"), ("long lasting", "longevity"),
    ("long-lasting", "longevity"), ("last long", "longevity"), ("lasts long", "longevity"),
    ("long term", "longevity"), ("long-term", "longevity"), ("longevity", "longevity"),
    ("software updates", "longevity"), ("updates", "longevity"), ("durable", "longevity"),
    ("for years", "longevity"), ("many years", "longevity"),
    ("video editing", "content_creation"), ("content creation", "content_creation"),
    ("content creator", "content_creation"), ("editing", "content_creation"), ("vlogging", "content_creation"),
    ("vlog", "content_creation"), ("design", "content_creation"),
    ("photography", "photography"), ("photos", "photography"), ("photo", "photography"),
    ("camera", "photography"), ("pictures", "photography"), ("zoom", "photography"), ("portrait", "photography"),
    ("gaming", "gaming"), ("games", "gaming"), ("game", "gaming"), ("gamer", "gaming"), ("pubg", "gaming"),
    ("free fire", "gaming"), ("freefire", "gaming"), ("genshin", "gaming"), ("cod", "gaming"),
    ("programming", "programming"), ("coding", "programming"), ("developer", "programming"),
    ("student", "student"), ("study", "student"), ("college", "student"), ("school", "student"),
    ("online class", "student"),
    ("business", "business"), ("office", "business"), ("work", "business"),
    ("fitness", "fitness"), ("running", "fitness"), ("gym", "fitness"), ("workout", "fitness"),
    ("social media", "everyday"), ("daily use", "everyday"), ("everyday", "everyday"), ("youtube", "everyday"),
    ("tiktok", "everyday"), ("calls", "everyday"),
    ("compact", "portability"), ("lightweight", "portability"), ("small", "portability"),
    ("one hand", "portability"), ("light", "portability"), ("portable", "portability"),
    ("display", "display"), ("screen", "display"), ("amoled", "display"),
    ("all rounder", "balanced"), ("all-rounder", "balanced"), ("balanced", "balanced"),
]

BRANDS: dict[str, str] = {  # word -> brand as stored
    "samsung": "samsung", "galaxy": "samsung", "apple": "apple", "iphone": "apple", "macbook": "apple",
    "xiaomi": "xiaomi", "redmi": "xiaomi", "poco": "xiaomi", "mi": "xiaomi", "oneplus": "oneplus",
    "google": "google", "pixel": "google", "oppo": "oppo", "vivo": "vivo", "iqoo": "vivo", "realme": "realme",
    "honor": "honor", "motorola": "motorola", "moto": "motorola", "nothing": "nothing", "infinix": "infinix",
    "tecno": "tecno", "huawei": "huawei", "lenovo": "lenovo", "hp": "hp", "dell": "dell", "asus": "asus",
    "acer": "acer", "msi": "msi", "anker": "anker", "ugreen": "ugreen", "baseus": "baseus",
}

OS_WORDS = {"android": "android", "ios": "ios", "iphone": "ios", "windows": "windows",
            "mac": "macos", "macos": "macos", "macbook": "macos", "chromebook": "chromeos", "linux": "linux"}

_AMOUNT = r"(?:rs\.?|npr|nrs\.?|रु\.?)?\s*(\d[\d,]*(?:\.\d+)?)\s*(k|lakh|lakhs|lac|l|thousand|hajar|hazar)?\b"


def _amount(num: str, unit: str | None) -> float:
    v = float(num.replace(",", ""))
    unit = (unit or "").lower()
    if unit in ("k", "thousand", "hajar", "hazar"):
        return v * 1000
    if unit in ("lakh", "lakhs", "lac", "l"):
        return v * 100_000
    return v * 1000 if v < 1000 else v  # "under 50" in a budget context means 50k


@dataclass
class Parsed:
    category: Category | None = None
    budget_min: float | None = None
    budget_max: float | None = None
    uses: list[str] = field(default_factory=list)          # in the order the buyer said them
    os: list[str] = field(default_factory=list)
    brands: list[str] = field(default_factory=list)
    exclude_brands: list[str] = field(default_factory=list)
    must: dict[str, tuple[str, Any]] = field(default_factory=dict)
    understood: list[str] = field(default_factory=list)    # human-readable, for "Understood: ..." chips

    def to_dict(self) -> dict:
        return {
            "category": self.category.value if self.category else None,
            "budget_min": self.budget_min, "budget_max": self.budget_max, "uses": self.uses,
            "os": self.os, "brands": self.brands, "exclude_brands": self.exclude_brands,
            "must": [{"key": k, "op": op, "value": v} for k, (op, v) in self.must.items()],
            "understood": self.understood,
        }


def _take(text: str, pattern: str) -> tuple[re.Match | None, str]:
    """Find a pattern and blank it out so later rules don't reuse the same words."""
    m = re.search(pattern, text)
    if not m:
        return None, text
    return m, text[:m.start()] + " " * (m.end() - m.start()) + text[m.end():]


def parse_query(text: str) -> Parsed:
    t = " " + re.sub(r"\s+", " ", text.lower().replace("₨", "rs").replace("–", "-")) + " "
    p = Parsed()

    # 1. Spec numbers first, so "5000mah" or "16gb" never looks like a budget.
    spec_rules = [
        (r"(\d{4,6})\s*mah", "battery_mah", ">=", "{v:,.0f} mAh battery"),
        (r"(\d{2,3})\s*w(?:att)?\b(?:\s*(?:fast\s*)?charg\w*)?", "charging_w", ">=", "{v:g} W charging"),
        (r"(\d{2,3})\s*hz", "refresh_rate_hz", ">=", "{v:g} Hz screen"),
        (r"(\d{1,2})\s*gb\s*ram|ram\s*(\d{1,2})\s*gb", "ram_gb", ">=", "{v:g} GB RAM"),
        (r"(\d{2,4})\s*gb(?:\s*storage)?|(\d)\s*tb", "storage_gb", ">=", "{v:g} GB storage"),
    ]
    for pattern, key, op, label in spec_rules:
        m, t = _take(t, pattern)
        if m:
            raw = next(g for g in m.groups() if g)
            v = float(raw) * (1024 if key == "storage_gb" and "tb" in m.group(0) else 1)
            p.must[key] = (op, v)
            p.understood.append(label.format(v=v))

    flags = [
        (r"\b5g\b", "has_5g", "5G"), (r"\bnfc\b", "has_nfc", "NFC"), (r"\bgps\b", "has_gps", "GPS"),
        (r"\bois\b|stabili[sz]\w*", "has_ois", "optical stabilisation"),
        (r"\brtx\b|\bgtx\b|dedicated (?:gpu|graphics)|graphics card", "has_dedicated_gpu", "dedicated graphics"),
    ]
    for pattern, key, label in flags:
        m, t = _take(t, pattern)
        if m:
            p.must[key] = ("==", True)
            p.understood.append(label)
    m, t = _take(t, r"water\s*(?:proof|resistant|resistance)|\bip6[78]\b|\b\d+\s*atm\b")
    if m:
        p.must["water_rating"] = (">=", 7)
        p.understood.append("water resistant")

    # 2. Budget.
    amt = _AMOUNT
    m, t = _take(t, rf"(?:between|from)\s+{amt}\s*(?:and|to|-)\s*{amt}")
    if not m:
        m, t = _take(t, rf"{amt}\s*(?:-|to)\s*{amt}")
    if m:
        g = m.groups()
        lo, hi = _amount(g[0], g[1] or g[3]), _amount(g[2], g[3])
        p.budget_min, p.budget_max = min(lo, hi), max(lo, hi)
    else:
        rules = [
            (rf"(?:under|below|less than|within|upto|up to|max(?:imum)?|budget(?: of| is)?|not more than|<)\s*{amt}", "max"),
            (rf"(?:above|over|more than|at least|min(?:imum)?|starting|>)\s*{amt}", "min"),
            (rf"(?:around|about|approx(?:imately)?|near|~)\s*{amt}", "around"),
            (rf"{amt}\s*(?:budget|ma|samma|bhitra)", "max"),   # Nepali: "50k samma", "1 lakh bhitra"
            (rf"(?:rs\.?|npr|रु\.?)\s*(\d[\d,]*(?:\.\d+)?)\s*(k|lakh|lakhs|lac|l|thousand|hajar|hazar)?\b", "max"),
            (r"\b(\d[\d,]*(?:\.\d+)?)\s*(k|lakh|lakhs|lac|thousand|hajar|hazar)\b", "max"),
            (r"\b(\d{1,3}(?:,\d{2,3})+|\d{5,7})\b", "max"),
        ]
        for pattern, kind in rules:
            m, t = _take(t, pattern)
            if not m:
                continue
            v = _amount(m.group(1), m.group(2) if m.lastindex and m.lastindex >= 2 else None)
            if kind == "max":
                p.budget_max = v
            elif kind == "min":
                p.budget_min = v
            else:
                p.budget_min, p.budget_max = round(v * 0.85, -2), round(v * 1.1, -2)
            break
    if p.budget_max or p.budget_min:
        lo = f"Rs {p.budget_min:,.0f}" if p.budget_min else None
        hi = f"Rs {p.budget_max:,.0f}" if p.budget_max else None
        p.understood.append(f"{lo} to {hi}" if lo and hi else f"up to {hi}" if hi else f"from {lo}")

    # 3. Brands to avoid ("no samsung", "except hp", "not apple"), then brands wanted.
    for m in list(re.finditer(r"\b(?:no|not|except|without|avoid)\s+(\w+)", t)):
        b = BRANDS.get(m.group(1))
        if b and b not in p.exclude_brands:
            p.exclude_brands.append(b)
            p.understood.append(f"not {b.upper() if len(b) <= 3 else b.title()}")
            t = t.replace(m.group(0), " " * len(m.group(0)))

    # 4. Category (before brands/OS so "iphone" also sets the category).
    for word, cat in CATEGORY_WORDS:
        if re.search(rf"\b{re.escape(word)}s?\b", t):
            p.category = cat
            break

    for word, os_name in OS_WORDS.items():
        if re.search(rf"\b{word}\b", t) and os_name not in p.os:
            p.os.append(os_name)
    for word, brand in BRANDS.items():
        if re.search(rf"\b{word}\b", t) and brand not in p.brands and brand not in p.exclude_brands:
            # "iphone"/"macbook" name a brand, but the OS already captures the preference.
            if word in ("iphone", "macbook", "galaxy", "pixel") and not re.search(r"\b(only|just)\b", t):
                continue
            p.brands.append(brand)
    if p.os:
        p.understood.append(" / ".join(o for o in p.os))
    if p.brands:
        p.understood.append("brand: " + ", ".join(b.title() for b in p.brands))

    # 5. Uses, in the order the buyer mentioned them (earlier = more important).
    found: list[tuple[int, str]] = []
    for phrase, use in USE_WORDS:
        for m in re.finditer(rf"\b{re.escape(phrase)}\b", t):
            if use not in [u for _, u in found]:
                found.append((m.start(), use))
            t = t[:m.start()] + " " * len(m.group(0)) + t[m.end():]
    # Accessories: "20000mah" / "65w" describe capacity and output, not a phone battery.
    if p.category in (C.POWER_BANK, C.CHARGER):
        for src, dst in (("battery_mah", "capacity_mah"), ("charging_w", "output_w")):
            if src in p.must:
                p.must[dst] = p.must.pop(src)
        p.understood = [u.replace("mAh battery", "mAh capacity").replace("W charging", "W output")
                        for u in p.understood]
    p.uses = [u for _, u in sorted(found)]
    head = [p.category.value.replace("_", " ")] if p.category else []
    p.understood = head + [u.replace("_", " ") for u in p.uses] + p.understood
    return p
