"""Core data model shared by every source, the normalizer, storage and scoring."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Category(str, Enum):
    PHONE = "phone"
    SMARTWATCH = "smartwatch"
    LAPTOP = "laptop"
    TABLET = "tablet"
    POWER_BANK = "power_bank"
    CHARGER = "charger"
    EARBUDS = "earbuds"
    CASE = "case"
    CABLE = "cable"
    ACCESSORY = "accessory"
    UNKNOWN = "unknown"


# Canonical spec keys. Sources emit raw strings; the normalizer maps them to these.
# Keeping one flat vocabulary lets scoring compare devices across sources.
SPEC_KEYS = {
    "os": str,               # "android", "ios", "windows", "macos", "chromeos", "wearos", "watchos", ...
    "chipset": str,
    "ram_gb": float,
    "storage_gb": float,
    "display_size_in": float,
    "refresh_rate_hz": float,
    "resolution_px": int,    # width * height, a single comparable number
    "battery_mah": float,
    "battery_wh": float,
    "charging_w": float,
    "main_camera_mp": float,
    "camera_count": int,
    "optical_zoom_x": float,
    "has_ois": bool,
    "weight_g": float,
    "water_resistance": str,  # "IP68", "5ATM", ...
    "gpu": str,
    "has_dedicated_gpu": bool,
    "capacity_mah": float,    # power banks
    "output_w": float,        # power banks / chargers
    "ports": str,
    "release_year": int,
    "benchmark_score": float,  # optional, from review sites that publish one
}


@dataclass
class Offer:
    """A price seen at a specific store at a specific time."""

    source: str
    url: str
    price: float | None
    currency: str | None
    in_stock: bool | None = None
    scraped_at: str | None = None


@dataclass
class Product:
    source: str
    url: str
    name: str
    brand: str | None = None
    category: Category = Category.UNKNOWN
    raw_specs: dict[str, str] = field(default_factory=dict)
    specs: dict[str, Any] = field(default_factory=dict)
    offers: list[Offer] = field(default_factory=list)
    rating: float | None = None        # normalized to 0-5
    review_count: int | None = None
    image: str | None = None

    @property
    def best_price(self) -> float | None:
        prices = [o.price for o in self.offers if o.price is not None]
        return min(prices) if prices else None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["category"] = self.category.value
        d["best_price"] = self.best_price
        return d
