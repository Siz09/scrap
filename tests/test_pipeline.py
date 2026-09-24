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


def test_phone_titles_with_a_sales_pitch_are_the_same_model():
    """Store titles pile specs after the model; they must land on the same card."""
    P = Category.PHONE
    same = [("OnePlus 12", "OnePlus 12 5G 54000mAh 50MP Triple Main Camera Smartphone"),
            ("OnePlus 13", "OnePlus 13 6.82-inch 50MP Sony LYT-808 50MP Qualcomm Snapdragon 8 Elite Smartphone"),
            ("OnePlus 15", "OnePlus 15 Snapdragon®8 Elite Gen 5 7300mAh 50MP"),
            ("OnePlus Nord 6", "OnePlus Nord 6 5G Smartphone Features and Specs"),
            ("OnePlus Nord CE 5", "OnePlus Nord CE5 5G 7100mAh Battery"),
            ("OnePlus Nord CE 4 Lite", "OnePlus Nord CE4 Lite 5G")]
    for a, b in same:
        assert canonical_key("OnePlus", a, P) == canonical_key("OnePlus", b, P), b
    assert canonical_key("OnePlus", "OnePlus 12R 5G", P) != canonical_key("OnePlus", "OnePlus 12", P)
    # Elsewhere the numbers are the model: power banks of different sizes stay apart.
    pb = Category.POWER_BANK
    assert canonical_key("UGREEN", "UGREEN 20000mAh Power Bank", pb) != canonical_key("UGREEN", "UGREEN 10000mAh Power Bank", pb)


def test_merged_card_keeps_the_plain_name(tmp_path):
    from devicescout.models import Offer, Product
    store = Store(tmp_path / "t.db")
    def phone(name, price):
        return Product(source="hukut", url=f"https://s/{price}", name=name, brand="OnePlus", category=Category.PHONE,
                       offers=[Offer(source="hukut", url=f"https://s/{price}", price=price, currency="NPR",
                                     scraped_at="2026-09-24T00:00:00+00:00")])
    k1 = store.upsert(phone("OnePlus 12 5G 54000mAh 50MP Triple Main Camera Smartphone", 139999))
    k2 = store.upsert(phone("OnePlus 12", 139999))
    assert k1 == k2
    [p] = store.products(Category.PHONE)
    assert p.name == "OnePlus 12"


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
    assert [r.product.name for r in budget] == ["Google Pixel 9"]

    assert rank(phones, "gaming", Category.PHONE, os="ios") == []
    assert all(0 <= r.coverage <= 1 and 0 <= r.score <= 100 for r in photo)


def test_empty_or_bad_rates_env_does_not_crash():
    import importlib
    import os

    import devicescout.currency as cur
    for value in ("", "not json"):
        os.environ["DEVICESCOUT_RATES"] = value
        try:
            importlib.reload(cur)
            assert cur.to_npr(1, "INR") == 1.6
        finally:
            del os.environ["DEVICESCOUT_RATES"]
    importlib.reload(cur)


@pytest.mark.parametrize("titles", [
    [("Samsung", "Samsung Galaxy A56 5G"), ("Samsung", "Samsung Galaxy A56 5G (8GB/256GB) - 1 Year Warranty"),
     ("Samsung", 'Galaxy A56 6.7" Super AMOLED, 50MP Camera'), (None, "Galaxy A56")],
    [("Xiaomi", "Xiaomi Redmi Note 14 Pro 5G"), ("Redmi", "Redmi Note 14 Pro (8/256)"),
     ("Xiaomi", "Redmi Note14 Pro MediaTek Dimensity 7300 Ultra")],
    [("Apple", "Apple iPhone 16 Pro Max 256GB Desert Titanium"), ("Apple", "iPhone16 Pro Max"), (None, "iPhone 16 Pro Max")],
    [("Infinix", "Infinix HOT 60 Pro Plus, 50MP Camera, Android 15 Smartphone"), ("Infinix", "Infinix Hot 60 Pro+"),
     ("Infinix", "Infinix HOT60 Pro+ 8+256GB")],
    [("Samsung", "Samsung Galaxy S25 Ultra AI Smartphone"), ("Samsung", "Samsung Galaxy S25 Ultra")],
    [("vivo", "Vivo Y29 4G"), ("vivo", "vivo Y29 (6GB+128GB) Mobile Phone")],
    [("Realme", "Realme 14 Pro+ 5G"), ("Realme", "realme 14 Pro Plus 5G 12/512")],
])
def test_one_key_per_model_across_brands_and_title_styles(titles):
    assert len({canonical_key(b, n, Category.PHONE) for b, n in titles}) == 1


def test_different_models_keep_different_keys():
    names = ["Samsung Galaxy S24", "Samsung Galaxy S24+", "Samsung Galaxy S24 Ultra", "Samsung Galaxy S24 FE",
             "Motorola Moto G (2024)", "Motorola Moto G (2025)", "Apple iPhone 16", "Apple iPhone 16 Plus",
             "Apple iPhone 16e", "OnePlus 12", "OnePlus 12R", "Koshi K5 Camera"]
    keys = [canonical_key(n.split()[0], n, Category.PHONE) for n in names]
    assert len(set(keys)) == len(keys)


def test_likely_same_flags_leftovers_but_not_other_models():
    from devicescout.normalize import likely_same
    assert likely_same("samsung galaxy a56", "samsung galaxy a56 awesome edition")
    assert not likely_same("samsung galaxy s24", "samsung galaxy s24 ultra")
    assert not likely_same("apple iphone 16", "apple iphone 16 pro max")


def test_processor_is_the_chip_not_the_core_layout():
    from devicescout.normalize import normalize_specs
    raw = {"Platform / CPU": "Octa-core (1x2.8 GHz Cortex-720 & 4x2.4 GHz Cortex-720)",
           "Platform / Chipset": "Qualcomm SM7635 Snapdragon 7s Gen 3 (4 nm)"}
    assert normalize_specs(raw, Category.PHONE)["chipset"].startswith("Qualcomm SM7635")
    assert normalize_specs({"CPU": "Octa-core 2.2 GHz"}, Category.PHONE)["chipset"] == "Octa-core 2.2 GHz"


@pytest.mark.parametrize("cat,a,b", [
    (Category.PHONE, "Honor 600 Lite 5G: Stunning", "Honor 600 Lite"),
    (Category.PHONE, "Apple iPhone 17 5G XDR", "Apple iPhone 17"),
    (Category.PHONE, "Xiaomi 17T 5G Leica Telephoto", "Xiaomi 17T"),
    (Category.EARBUDS, "Xiaomi Redmi Buds 8 True Wireless Earbuds Features", "Redmi Buds 8"),
    (Category.CAMERA, "Logitech C270 HD Webcam for Clear Video Calls", "Logitech C270 HD WebCam"),
])
def test_page_title_leftovers_are_the_same_model(cat, a, b):
    assert canonical_key(a.split()[0], a, cat) == canonical_key(b.split()[0], b, cat)


def test_taglines_do_not_merge_different_models():
    P = Category.PHONE
    assert canonical_key("Samsung", "Samsung Galaxy S26 FE: 5G", P) != canonical_key("Samsung", "Samsung Galaxy S26", P)
    assert canonical_key("Honor", "Honor X9c Smart 5G", P) != canonical_key("Honor", "Honor X9c 5G", P)
    assert "iphone 16" in canonical_key("Spigen", "Spigen Case for iPhone 16", Category.CASE)
