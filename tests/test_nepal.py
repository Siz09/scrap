"""Nepal-specific sources, pricing and the needs-based advisor (offline fixtures)."""

import json
from pathlib import Path

import pytest
from scrapling.parser import Selector

from devicescout.advisor import Needs, advise, blend_weights
from devicescout.models import Category, Offer, Product
from devicescout.normalize import canonical_key, parse_label_lines, parse_price, parse_variant
from devicescout.pricing import flag_suspicious
from devicescout.sources import GenericSource, GSMArenaSource, ShopifySource, SiteConfig, WooCommerceSource
from devicescout.sources.daraz import extract_items, item_to_product
from devicescout.sources.generic import price_from_text
from devicescout.storage import Store

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def page(name, url="https://example.com.np/p"):
    return Selector((FIX / name).read_text(encoding="utf-8"), url=url)


# --- parsing helpers ---------------------------------------------------------

@pytest.mark.parametrize("text,value", [
    ("Rs. 1,49,999", 149999), ("NPR 24,999", 24999), ("रु 57,999", 57999), ("1.5 lakh", 150000),
    ("Rs. 24,999 - Rs. 27,999", 24999), ("1,299.00", 1299), ("1.349,00", 1349), (38999, 38999),
    ("52999.00", 52999),
])
def test_parse_price_nepali_formats(text, value):
    assert parse_price(text) == value


@pytest.mark.parametrize("text,variant", [
    ("Redmi Note 14 Pro 5G (8GB/256GB)", "8/256"), ("8GB / 128GB", "8/128"), ("12GB RAM 512GB", "12/512"),
    ("Galaxy S24 (8+256GB)", "8/256"), ("iPhone 16 128GB", "128"), ("12/1TB", "12/1TB"),
])
def test_parse_variant(text, variant):
    assert parse_variant(text) == variant


def test_label_lines_from_descriptions():
    raw = parse_label_lines('Display: 6.67" AMOLED 120Hz\n• RAM : 8GB\n- Battery - 5500mAh\nWarranty 1 year')
    assert raw == {"Display": '6.67" AMOLED 120Hz', "RAM": "8GB", "Battery": "5500mAh"}


def test_canonical_key_handles_marketplace_titles():
    k = canonical_key("Xiaomi", "Redmi Note 14 Pro 5G (8GB/256GB) - 1 Year Official Warranty")
    assert k == canonical_key("Xiaomi", "Redmi Note 14 Pro 5G 8/256") == "xiaomi redmi note 14 pro"
    assert canonical_key("Samsung", "Samsung Galaxy A56 5G Price in Nepal, Specs & Availability") == \
        canonical_key("Samsung", "Samsung Galaxy A56") == "samsung galaxy a56"


# --- sources -----------------------------------------------------------------

def test_daraz_listing_json():
    items = extract_items(load("daraz_catalog.json"))
    assert len(items) == 6
    p = item_to_product(items[0], category_hint="mobile phones")
    assert p.name == "Redmi Note 14 Pro 5G (8GB/256GB)"
    assert p.category == Category.PHONE and p.brand == "Xiaomi"
    o = p.offers[0]
    assert o.price == 38999 and o.currency == "NPR" and o.original_price == 42999
    assert o.official is True and o.seller == "Xiaomi Official Store" and o.variant == "8/256"
    assert o.url == "https://www.daraz.com.np/products/redmi-note-14-pro-i101.html"
    assert p.rating == 4.6 and p.review_count == 212 and p.specs["has_5g"] is True

    clone = item_to_product(items[4], category_hint="mobile phones")
    assert clone.brand is None  # "No Brand"

    bank = item_to_product(items[5], category_hint="power bank")
    assert bank.category == Category.POWER_BANK and bank.offers[0].in_stock is False


def test_shopify_products_json():
    src = ShopifySource(name="brother-mart", base_url="https://brother-mart.com")
    prods = [src.parse_product(p, "smartphones") for p in load("shopify_products.json")["products"]]
    phone, case = prods
    assert phone.category == Category.PHONE and case.category == Category.CASE
    assert phone.gtin == "8806095467884"
    assert [(o.variant, o.price, o.original_price) for o in phone.offers] == [("8/128", 52999, None), ("8/256", 57999, 59999)]
    assert phone.url == "https://brother-mart.com/products/samsung-galaxy-a56-5g"
    s = phone.specs
    assert s["battery_mah"] == 5000 and s["charging_w"] == 45 and s["refresh_rate_hz"] == 120
    assert s["main_camera_mp"] == 50 and s["has_ois"] is True and s["water_resistance"] == "IP67"
    assert s["ram_gb"] == 8 and s["storage_gb"] == 256 and s["weight_g"] == 198


def test_woocommerce_store_api():
    src = WooCommerceSource(name="nepal-laptops", base_url="https://store.example.com.np")
    p = src.parse_product(load("woo_products.json")[0])
    assert p.category == Category.LAPTOP
    o = p.offers[0]
    assert o.price == 109999 and o.original_price == 114999 and o.currency == "NPR"
    s = p.specs
    assert s["ram_gb"] == 16 and s["storage_gb"] == 512 and s["battery_wh"] == 57 and s["weight_g"] == 1460
    assert s["has_dedicated_gpu"] is False and s["os"] == "windows" and "Core Ultra 5" in s["chipset"]
    assert s["resolution_px"] == 1920 * 1200


def test_gadgetbyte_reference_price_from_text():
    cfg = SiteConfig(name="gadgetbyte", region="np-ref", price_from_text=True, currency="NPR")
    p = GenericSource(cfg).parse(page("gadgetbyte_a56.html"))
    assert p.name.startswith("Samsung Galaxy A56 5G")
    assert p.offers[0].price == 54999 and p.offers[0].region == "np-ref"
    assert p.specs["has_5g"] is True and p.specs["has_nfc"] is True
    assert price_from_text("no price here") is None


def test_review_site_expert_score():
    p = GenericSource(SiteConfig(name="notebookcheck", region="intl")).parse(page("notebookcheck_review.html"))
    assert p.name == "Samsung Galaxy A56" and p.brand == "Samsung"
    assert p.specs["expert_score"] == 86 and p.offers == []


# --- pricing -----------------------------------------------------------------

def test_suspicious_prices_and_clones_are_flagged(tmp_path):
    store = Store(tmp_path / "t.db")
    for item in extract_items(load("daraz_catalog.json")):
        store.upsert(item_to_product(item, category_hint="mobile phones"))
    phones = {p.name: p for p in store.products(Category.PHONE)}
    redmi = next(p for n, p in phones.items() if n.startswith("Redmi Note 14 Pro"))
    assert len(redmi.offers) == 4
    flagged = [o.seller for o in redmi.offers if o.suspicious]
    assert flagged == ["Cheap Deals KTM"]             # Rs 21,999 vs ~Rs 39,000 elsewhere
    assert redmi.best_price == 38999
    clone = next(p for n, p in phones.items() if n.startswith("i16"))
    assert clone.best_price is None                   # clone listing never counts as a price


def test_international_reference_flags_too_cheap_local_offer():
    p = Product(source="x", url="u", name="Galaxy S24 Ultra", offers=[
        Offer("daraz", "a", 70000, "NPR"), Offer("intl", "b", 1100, "USD", region="intl")])
    flag_suspicious(p)
    assert p.offers[0].suspicious and p.best_price is None


# --- advisor -----------------------------------------------------------------

def phone(name, price, official=True, **specs):
    return Product(source="t", url=f"https://x/{name}", name=name, brand=name.split()[0],
                   category=Category.PHONE, specs={"os": "android", **specs},
                   offers=[Offer("shop", f"https://shop/{name}", price, "NPR", official=official, seller="Shop")])


@pytest.fixture
def catalogue():
    return [
        phone("Alpha Cam", 58000, optical_zoom_x=3, has_ois=True, main_camera_mp=50, camera_count=3,
              battery_mah=4700, charging_w=25, chipset="Snapdragon 7s Gen 3", has_5g=True, expert_score=84),
        phone("Beta Battery", 42000, optical_zoom_x=None, has_ois=False, main_camera_mp=50, camera_count=2,
              battery_mah=6500, charging_w=67, chipset="Dimensity 7300", has_5g=True, expert_score=78),
        phone("Gamma Budget", 25000, has_ois=False, main_camera_mp=50, camera_count=2, battery_mah=5000,
              charging_w=18, chipset="Helio G85", has_5g=False),
        phone("Delta Flagship", 66000, optical_zoom_x=5, has_ois=True, main_camera_mp=200, camera_count=4,
              battery_mah=5000, charging_w=45, chipset="Snapdragon 8 Gen 3", has_5g=True, expert_score=92),
        phone("Omega Pricey", 150000, optical_zoom_x=5, has_ois=True, battery_mah=5000, chipset="Snapdragon 8 Elite"),
        Product(source="t", url="u", name="Nokia Phone Case", category=Category.CASE,
                offers=[Offer("s", "u", 900, "NPR")]),
    ]


def test_advise_photography_within_budget(catalogue):
    a = advise(catalogue, Needs(category=Category.PHONE, budget_max=60000, uses={"photography": 1}))
    names = [p.ranked.product.name for p in a.picks]
    assert names[0] == "Alpha Cam"
    assert "Delta Flagship" not in names and "Omega Pricey" not in names
    assert a.excluded["over_budget"] == 1               # Omega; Delta is within stretch range
    assert a.stretch_pick and a.stretch_pick.ranked.product.name == "Delta Flagship"
    assert any("optical zoom" in s for s in a.picks[0].strengths)
    assert a.picks[0].where_to_buy[0]["price_npr"] == 58000


def test_advise_battery_use_changes_the_winner(catalogue):
    a = advise(catalogue, Needs(category=Category.PHONE, budget_max=60000, uses={"battery": 1}))
    assert a.picks[0].ranked.product.name == "Beta Battery"


def test_advise_must_haves_and_budget_floor(catalogue):
    a = advise(catalogue, Needs(category=Category.PHONE, budget_min=30000, budget_max=60000,
                                must={"has_5g": ("==", True), "battery_mah": (">=", 5000)}))
    assert [p.ranked.product.name for p in a.picks] == ["Beta Battery"]
    assert a.excluded["under_min_budget"] == 1 and a.excluded["must_have"] == 1


def test_advise_unknown_must_have_is_flagged_not_dropped(catalogue):
    a = advise(catalogue, Needs(category=Category.PHONE, budget_max=60000, must={"has_nfc": ("==", True)}))
    assert len(a.picks) == 3
    assert all(any("could not confirm: NFC" in w for w in p.warnings) for p in a.picks)


def test_advise_value_pick(catalogue):
    a = advise(catalogue, Needs(category=Category.PHONE, budget_max=60000, uses={"everyday": 1}))
    if a.value_pick:
        assert a.value_pick.price < a.picks[0].price


def test_blend_weights_sum_to_one():
    w = blend_weights(Category.PHONE, {"photography": 2, "gaming": 1})
    assert abs(sum(x for _, x, _ in w) - 1) < 1e-9


def test_budget_parsing():
    assert Needs.parse_budget("30k-60k") == (30000, 60000)
    assert Needs.parse_budget("1.5 lakh") == (None, 150000)
    assert Needs.parse_budget("Rs 45,000") == (None, 45000)
    assert Needs.parse_budget("40000-") == (40000, None)


def test_end_to_end_merge_specs_from_gsmarena_prices_from_nepal(tmp_path):
    """The core idea: specs from a spec DB, prices from Nepali stores, joined by model."""
    store = Store(tmp_path / "t.db")
    shop = ShopifySource(name="brother-mart", base_url="https://brother-mart.com")
    store.upsert(shop.parse_product(load("shopify_products.json")["products"][0], "smartphones"))
    store.upsert(GenericSource(SiteConfig(name="gadgetbyte", region="np-ref", price_from_text=True,
                                          currency="NPR")).parse(page("gadgetbyte_a56.html")))
    store.upsert(GenericSource(SiteConfig(name="notebookcheck", region="intl")).parse(page("notebookcheck_review.html")))
    [a56] = store.products(Category.PHONE)
    assert a56.best_price == 52999
    assert {o.source for o in a56.offers} == {"brother-mart", "gadgetbyte"}
    assert a56.specs["expert_score"] == 86 and a56.specs["battery_mah"] == 5000
    assert a56.gtin == "8806095467884"


def test_os_inferred_from_model_names():
    from devicescout.normalize import infer_os
    assert infer_os(Category.PHONE, "Apple iPhone 16 Pro") == "ios"
    assert infer_os(Category.PHONE, "Xiaomi Redmi Note 14") == "android"
    assert infer_os(Category.PHONE, "Huawei Pura 70") is None
    assert infer_os(Category.LAPTOP, "Apple MacBook Air M3") == "macos"
    assert infer_os(Category.LAPTOP, "Lenovo IdeaPad Slim 5") is None


def test_unknown_os_is_warned_not_excluded(catalogue):
    catalogue[0].specs.pop("os")
    a = advise(catalogue, Needs(category=Category.PHONE, budget_max=60000, os=["android"]))
    alpha = next(p for p in a.picks if p.ranked.product.name == "Alpha Cam")
    assert any("operating system" in w for w in alpha.warnings)
