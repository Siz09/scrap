from pathlib import Path

import pytest
from scrapling.parser import Selector

from devicescout.models import Category
from devicescout.normalize import canonical_key, categorize
from devicescout.scoring import chip_tier, rank
from devicescout.sources import GenericSource, GSMArenaSource, SiteConfig
from devicescout.storage import Store

FIX = Path(__file__).parent / "fixtures"


def page(name: str, url: str = "https://example.com/p") -> Selector:
    return Selector((FIX / name).read_text(encoding="utf-8"), url=url)


def generic(name: str, **cfg):
    return GenericSource(SiteConfig(name=cfg.pop("site", "shop"), **cfg)).parse(page(name))


def test_gsmarena_phone_specs():
    p = GSMArenaSource().parse(page("gsmarena_s24_ultra.html"))
    assert p.name == "Samsung Galaxy S24 Ultra"
    assert p.brand == "Samsung"
    assert p.category == Category.PHONE
    s = p.specs
    assert s["os"] == "android"
    assert "Snapdragon 8 Gen 3" in s["chipset"]
    assert s["ram_gb"] == 12 and s["storage_gb"] == 1024
    assert s["display_size_in"] == 6.8
    assert s["refresh_rate_hz"] == 120
    assert s["resolution_px"] == 1440 * 3120
    assert s["battery_mah"] == 5000
    assert s["charging_w"] == 45
    assert s["main_camera_mp"] == 200
    assert s["camera_count"] == 4          # selfie camera excluded
    assert s["optical_zoom_x"] == 5
    assert s["has_ois"] is True
    assert s["weight_g"] == 232
    assert s["water_resistance"] == "IP68"
    assert s["release_year"] == 2024


def test_gsmarena_watch_is_categorized_as_smartwatch():
    p = GSMArenaSource().parse(page("gsmarena_pixel_watch.html"))
    assert p.category == Category.SMARTWATCH
    assert p.specs["os"] == "wearos"
    assert p.specs["battery_mah"] == 307
    assert p.specs["weight_g"] == 31


def test_generic_jsonld_graph_and_price():
    p = generic("store_s24_ultra.html")
    assert p.brand == "Samsung"
    assert p.category == Category.PHONE
    assert p.offers[0].price == 1099.99
    assert p.offers[0].currency == "USD" and p.offers[0].in_stock is True
    assert p.best_price == 1099.99 * 140      # converted to NPR
    assert p.rating == 4.6 and p.review_count == 1289
    assert p.specs["battery_mah"] == 4900


def test_generic_additional_property():
    p = generic("store_pixel9.html")
    assert p.brand == "Google"
    assert p.specs["chipset"] == "Google Tensor G4"
    assert p.specs["has_ois"] is True and p.specs["main_camera_mp"] == 50
    assert p.specs["battery_mah"] == 4700 and p.specs["charging_w"] == 27
    assert p.specs["refresh_rate_hz"] == 120


def test_generic_laptop_eu_price_and_dl_specs():
    p = generic("store_laptop.html")
    assert p.category == Category.LAPTOP
    assert p.offers[0].price == 1349.0 and p.offers[0].currency == "EUR"
    assert p.offers[0].in_stock is False
    assert p.rating == 4.5                  # 9/10 -> 4.5/5
    s = p.specs
    assert s["ram_gb"] == 16 and s["storage_gb"] == 1024
    assert s["has_dedicated_gpu"] is True
    assert s["refresh_rate_hz"] == 165
    assert s["battery_wh"] == 80 and s["weight_g"] == 2400
    assert s["os"] == "windows"


def test_generic_power_bank_custom_rows():
    p = generic("store_powerbank.html", spec_row_css=".specs li",
                spec_label_css=".spec-name", spec_value_css=".spec-value")
    assert p.category == Category.POWER_BANK
    assert p.specs["capacity_mah"] == 24000
    assert p.specs["output_w"] == 140
    assert p.specs["weight_g"] == 630


@pytest.mark.parametrize("name,cat", [
    ("Spigen Tough Armor Case for iPhone 16 Pro", Category.CASE),
    ("Anker 737 Power Bank", Category.POWER_BANK),
    ("Apple Watch Series 10", Category.SMARTWATCH),
    ("Apple MacBook Air 13 M3", Category.LAPTOP),
    ("Samsung Galaxy Tab S9", Category.TABLET),
    ("Xiaomi 14T Pro", Category.PHONE),
    ("UGREEN USB-C to USB-C Cable 240W", Category.CABLE),
])
def test_categorize(name, cat):
    assert categorize(name) == cat


def test_canonical_key_merges_variants():
    a = canonical_key("Samsung", "Samsung Galaxy S24 Ultra 5G 256GB Titanium Black (Unlocked)")
    b = canonical_key("Samsung", "Samsung Galaxy S24 Ultra")
    c = canonical_key("Samsung", "Galaxy S24 Ultra (12GB/512GB)")
    assert a == b == c == "samsung galaxy s24 ultra"
    assert canonical_key("Google", "Google Pixel 9 128GB Obsidian") == "google pixel 9"


def test_chip_tier():
    assert chip_tier("Snapdragon 8 Gen 3") == 9
    assert chip_tier("Google Tensor G4") == 8
    assert chip_tier("AMD Ryzen 7 8845HS") == 8
    assert chip_tier("mystery chip") is None


def test_store_merges_sources_with_spec_priority(tmp_path):
    store = Store(tmp_path / "t.db")
    k1 = store.upsert(GSMArenaSource().parse(page("gsmarena_s24_ultra.html")))
    k2 = store.upsert(generic("store_s24_ultra.html"))
    assert k1 == k2
    [p] = store.products(Category.PHONE)
    assert p.specs["battery_mah"] == 5000       # spec DB beats the retailer's 4,900
    assert p.offers[0].price == 1099.99         # price comes from the retailer
    assert p.rating == 4.6
    assert p.name == "Samsung Galaxy S24 Ultra"


def test_rank_profiles(tmp_path):
    store = Store(tmp_path / "t.db")
    store.upsert(GSMArenaSource().parse(page("gsmarena_s24_ultra.html")))
    store.upsert(generic("store_s24_ultra.html"))
    store.upsert(generic("store_pixel9.html"))
    phones = store.products(Category.PHONE)

    photo = rank(phones, "photography", Category.PHONE)
    assert photo[0].product.name == "Samsung Galaxy S24 Ultra"

    compact = rank(phones, "portability", Category.PHONE)
    assert compact[0].product.name.startswith("Google Pixel 9")

    budget = rank(phones, "balanced", Category.PHONE, max_price=800 * 140)  # NPR
    assert [r.product.name for r in budget] == ["Google Pixel 9 128GB Obsidian"]

    assert rank(phones, "gaming", Category.PHONE, os="ios") == []
    assert all(0 <= r.coverage <= 1 and 0 <= r.score <= 100 for r in photo)
