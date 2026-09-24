"""A fictional sample catalogue for trying the app before scraping anything.

Brands, models, stores and prices are invented. The UI shows a "sample data" banner
whenever this catalogue is in use, so it can't be mistaken for real prices.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Category, Offer, Product
from .storage import Store, open_store

C = Category
STORES = [("Sample Store Kathmandu", True), ("Sample Gadget Pasal", False), ("Sample Marketplace", None)]

# (category, name, brand, price_npr, specs, rating, reviews)
DEVICES: list[tuple] = [
    (C.PHONE, "Nimbus Z9 Ultra", "Nimbus", 179999, dict(os="android", chipset="Snapdragon 8 Elite", ram_gb=12, storage_gb=512,
     display_size_in=6.8, refresh_rate_hz=120, resolution_px=1440 * 3120, main_camera_mp=200, camera_count=4, optical_zoom_x=5,
     has_ois=True, battery_mah=5000, charging_w=45, weight_g=218, water_resistance="IP68", has_5g=True, has_nfc=True,
     release_year=2026, expert_score=91), 4.7, 310),
    (C.PHONE, "Everest P8 Pro", "Everest", 104999, dict(os="android", chipset="Snapdragon 8 Gen 3", ram_gb=12, storage_gb=256,
     display_size_in=6.7, refresh_rate_hz=120, resolution_px=1440 * 3200, main_camera_mp=50, camera_count=3, optical_zoom_x=3,
     has_ois=True, battery_mah=5100, charging_w=100, weight_g=205, water_resistance="IP68", has_5g=True, has_nfc=True,
     release_year=2025, expert_score=88), 4.6, 128),
    (C.PHONE, "Koshi K5 Camera", "Koshi", 57999, dict(os="android", chipset="Snapdragon 7s Gen 3", ram_gb=8, storage_gb=256,
     display_size_in=6.6, refresh_rate_hz=120, resolution_px=1080 * 2400, main_camera_mp=50, camera_count=3, optical_zoom_x=3,
     has_ois=True, battery_mah=4800, charging_w=33, weight_g=190, water_resistance="IP67", has_5g=True, has_nfc=True,
     release_year=2026, expert_score=84), 4.5, 92),
    (C.PHONE, "Lumo Power 7", "Lumo", 42999, dict(os="android", chipset="Dimensity 7300", ram_gb=8, storage_gb=256,
     display_size_in=6.78, refresh_rate_hz=120, resolution_px=1080 * 2400, main_camera_mp=50, camera_count=2,
     has_ois=False, battery_mah=6500, charging_w=67, weight_g=204, water_resistance="IP54", has_5g=True, has_nfc=False,
     release_year=2026, expert_score=79), 4.4, 540),
    (C.PHONE, "Terai Neo 3", "Terai", 32999, dict(os="android", chipset="Dimensity 7025", ram_gb=6, storage_gb=128,
     display_size_in=6.7, refresh_rate_hz=120, resolution_px=1080 * 2400, main_camera_mp=64, camera_count=2,
     has_ois=True, battery_mah=5000, charging_w=45, weight_g=185, has_5g=True, release_year=2025), 4.2, 77),
    (C.PHONE, "Koshi K3 Lite", "Koshi", 21999, dict(os="android", chipset="Helio G99", ram_gb=6, storage_gb=128,
     display_size_in=6.74, refresh_rate_hz=90, resolution_px=720 * 1600, main_camera_mp=50, camera_count=2,
     has_ois=False, battery_mah=5000, charging_w=18, weight_g=192, has_5g=False, release_year=2025, expert_score=68), 4.1, 860),
    (C.PHONE, "Nimbus Mini S", "Nimbus", 88999, dict(os="android", chipset="Snapdragon 8 Gen 3", ram_gb=8, storage_gb=256,
     display_size_in=6.1, refresh_rate_hz=120, resolution_px=1080 * 2340, main_camera_mp=50, camera_count=3, optical_zoom_x=3,
     has_ois=True, battery_mah=4000, charging_w=25, weight_g=167, water_resistance="IP68", has_5g=True, has_nfc=True,
     release_year=2025, expert_score=86), 4.5, 64),
    (C.PHONE, "Orchid One", "Orchid", 139999, dict(os="ios", chipset="A18 Pro", ram_gb=8, storage_gb=256,
     display_size_in=6.3, refresh_rate_hz=120, resolution_px=1206 * 2622, main_camera_mp=48, camera_count=3, optical_zoom_x=5,
     has_ois=True, battery_mah=3582, charging_w=30, weight_g=199, water_resistance="IP68", has_5g=True, has_nfc=True,
     release_year=2025, expert_score=90), 4.8, 205),
    (C.LAPTOP, "Everest Book 14 Air", "Everest", 124999, dict(os="windows", chipset="Intel Core Ultra 7 155H", ram_gb=16,
     storage_gb=1024, display_size_in=14, refresh_rate_hz=90, resolution_px=2880 * 1800, battery_wh=75, weight_g=1200,
     has_dedicated_gpu=False, release_year=2025, expert_score=87), 4.6, 41),
    (C.LAPTOP, "Himal Strike 16", "Himal", 189999, dict(os="windows", chipset="AMD Ryzen 7 8845HS", gpu="NVIDIA GeForce RTX 4070",
     ram_gb=32, storage_gb=1024, display_size_in=16, refresh_rate_hz=240, resolution_px=2560 * 1600, battery_wh=90,
     weight_g=2500, has_dedicated_gpu=True, release_year=2025, expert_score=85), 4.5, 58),
    (C.LAPTOP, "Himal Play 15", "Himal", 109999, dict(os="windows", chipset="Intel Core i5-13450HX", gpu="NVIDIA GeForce RTX 4050",
     ram_gb=16, storage_gb=512, display_size_in=15.6, refresh_rate_hz=144, resolution_px=1920 * 1080, battery_wh=60,
     weight_g=2300, has_dedicated_gpu=True, release_year=2024, expert_score=78), 4.3, 120),
    (C.LAPTOP, "Lumo Study 14", "Lumo", 64999, dict(os="windows", chipset="AMD Ryzen 5 7530U", ram_gb=8, storage_gb=512,
     display_size_in=14, refresh_rate_hz=60, resolution_px=1920 * 1080, battery_wh=50, weight_g=1450,
     has_dedicated_gpu=False, release_year=2024, expert_score=74), 4.2, 230),
    (C.LAPTOP, "Orchid Slate 13", "Orchid", 164999, dict(os="macos", chipset="M4", ram_gb=16, storage_gb=512,
     display_size_in=13.6, refresh_rate_hz=60, resolution_px=2560 * 1664, battery_wh=53, weight_g=1240,
     has_dedicated_gpu=False, release_year=2025, expert_score=92), 4.8, 96),
    (C.TABLET, "Koshi Tab 11", "Koshi", 39999, dict(os="android", chipset="Snapdragon 7s Gen 2", ram_gb=8, storage_gb=128,
     display_size_in=11, refresh_rate_hz=120, battery_mah=8000, release_year=2025), 4.3, 45),
    (C.TABLET, "Orchid Pad Air", "Orchid", 94999, dict(os="ipados", chipset="M2", ram_gb=8, storage_gb=128,
     display_size_in=11, refresh_rate_hz=60, battery_mah=7600, release_year=2024, expert_score=88), 4.7, 70),
    (C.SMARTWATCH, "Nimbus Watch 4", "Nimbus", 29999, dict(os="wearos", has_gps=True, has_nfc=True, battery_mah=425,
     weight_g=33, water_resistance="5ATM", release_year=2025), 4.4, 88),
    (C.SMARTWATCH, "Terai Fit Band 2", "Terai", 6499, dict(has_gps=False, battery_mah=300, weight_g=24,
     water_resistance="5ATM", release_year=2025), 4.1, 410),
    (C.SMARTWATCH, "Everest Trail GPS", "Everest", 44999, dict(os="wearos", has_gps=True, has_nfc=True, battery_mah=590,
     weight_g=52, water_resistance="10ATM", release_year=2025, expert_score=86), 4.6, 37),
    (C.POWER_BANK, "Lumo Pocket 10K", "Lumo", 2499, dict(capacity_mah=10000, output_w=22.5, weight_g=210), 4.3, 690),
    (C.POWER_BANK, "Everest Charge 20K", "Everest", 4999, dict(capacity_mah=20000, output_w=65, weight_g=420), 4.6, 180),
    (C.POWER_BANK, "Himal Laptop Bank 25K", "Himal", 10999, dict(capacity_mah=25000, output_w=140, weight_g=630), 4.7, 95),
    (C.EARBUDS, "Koshi Buds 2", "Koshi", 3999, dict(release_year=2025), 4.2, 330),
]


# Promised major OS upgrades (fictional), so "lasts for years" has something to rank on.
OS_UPGRADES = {"Nimbus Z9 Ultra": 7, "Everest P8 Pro": 5, "Koshi K5 Camera": 4, "Lumo Power 7": 3,
               "Terai Neo 3": 2, "Koshi K3 Lite": 2, "Nimbus Mini S": 7, "Orchid One": 6,
               "Koshi Tab 11": 3, "Orchid Pad Air": 6}


# Fictional sales, one per verdict the Deals page can show:
# name -> (store index, price factor vs normal, crossed-out factor or None, sale ends in N days or None)
SALES = {
    "Koshi K5 Camera": (1, 0.86, 1.10, 9),        # genuinely cheaper than other sellers: real deal
    "Everest Book 14 Air": (0, 0.95, 1.12, None),  # modest real saving: good price
    "Lumo Power 7": (1, 1.03, 1.30, 5),           # "30% off" but no cheaper than elsewhere: paper discount
    "Everest Charge 20K": (0, 0.99, 1.80, None),   # crossed-out price nobody ever charged: inflated
    "Nimbus Watch 4": (0, 0.88, None, None),       # quiet price cut, no banner: still a real deal
}


def build_sample(path):
    """Write the sample catalogue to a SQLite file (replaced) or a postgresql:// database (emptied)."""
    from .pgstore import is_postgres
    if is_postgres(path):
        store = open_store(path)
        store.reset_catalogue()
    else:
        path = Path(path)
        if path.exists():
            path.unlink()
        store = Store(path)
    now = datetime.now(timezone.utc)
    for i, (cat, name, brand, price, specs, rating, reviews) in enumerate(DEVICES):
        if name in OS_UPGRADES:
            specs = {**specs, "os_upgrades": OS_UPGRADES[name]}
        offers = []
        for j, (seller, official) in enumerate(STORES):
            if (i + j) % 3 == 2 and j:  # not every store stocks everything
                continue
            p = round(price * (1 + 0.03 * j) / 100) * 100 - 1
            sale = SALES.get(name)
            on_sale = sale is not None and sale[0] == j
            # A little price history: about the same a month ago, then today's price.
            for days, bump in ((30, 1.01), (0, 1.0)):
                today = days == 0
                factor = sale[1] if on_sale and today else bump
                offers.append(Offer(
                    source=seller.lower().replace(" ", "-"), url=f"https://example.com/sample/{i}/{j}",
                    price=round(p * factor), currency="NPR", in_stock=(i + j) % 5 != 4 or on_sale,
                    scraped_at=(now - timedelta(days=days)).isoformat(timespec="seconds"),
                    region="np", seller=seller, official=official,
                    original_price=round(p * sale[2]) if on_sale and today and sale[2] else None,
                    valid_until=((now + timedelta(days=sale[3])).date().isoformat()
                                 if on_sale and today and sale[3] else None),
                ))
        if cat == C.PHONE and i % 2 == 0:  # a bait listing, to show fake-price detection
            offers.append(Offer(source="sample-marketplace", url=f"https://example.com/sample/{i}/bait",
                                price=round(price * 0.35), currency="NPR", in_stock=True,
                                scraped_at=now.isoformat(timespec="seconds"), seller="Too Good To Be True Deals"))
        store.upsert(Product(source="sample", url=f"https://example.com/sample/{i}", name=name, brand=brand,
                             category=cat, specs=specs, offers=offers, rating=rating, review_count=reviews))
    store.close()
    return path
