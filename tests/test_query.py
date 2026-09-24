"""Plain-language requests -> structured needs."""

import pytest

from devicescout.models import Category
from devicescout.normalize import parse_os_upgrades
from devicescout.query import parse_query


@pytest.mark.parametrize("text,cat,lo,hi,uses", [
    ("photography phone under 120000", Category.PHONE, None, 120000, ["photography"]),
    ("photography phone under 1.2 lakh", Category.PHONE, None, 120000, ["photography"]),
    ("camera ramro phone 40k samma", Category.PHONE, None, 40000, ["photography"]),
    ("i want a gaming phone below 50k", Category.PHONE, None, 50000, ["gaming"]),
    ("long lasting phone around 60k", Category.PHONE, 51000, 66000, ["longevity"]),
    ("gaming laptop between 1 lakh and 1.5 lakh", Category.LAPTOP, 100000, 150000, ["gaming"]),
    ("laptop 80k-1.2 lakh for coding", Category.LAPTOP, 80000, 120000, ["programming"]),
    ("power bank under 5 hajar", Category.POWER_BANK, None, 5000, []),
    ("phone for photos and long battery, Rs 1,50,000", Category.PHONE, None, 150000, ["photography", "battery"]),
    ("battery first then camera phone under 50", Category.PHONE, None, 50000, ["battery", "photography"]),
])
def test_category_budget_uses(text, cat, lo, hi, uses):
    p = parse_query(text)
    assert (p.category, p.budget_min, p.budget_max, p.uses) == (cat, lo, hi, uses)


def test_specs_are_not_mistaken_for_budget():
    p = parse_query("phone with 5000mah 16gb ram 120hz 67w under 60k")
    assert p.budget_max == 60000
    assert p.must == {"battery_mah": (">=", 5000), "ram_gb": (">=", 16), "refresh_rate_hz": (">=", 120),
                      "charging_w": (">=", 67)}


def test_accessory_numbers_mean_capacity_and_output():
    p = parse_query("20000mah 65w power bank")
    assert p.must == {"capacity_mah": (">=", 20000), "output_w": (">=", 65)}


def test_flags_brands_and_os():
    p = parse_query("android phone with 5g nfc waterproof, no samsung, redmi only")
    assert p.must["has_5g"] == ("==", True) and p.must["has_nfc"] == ("==", True)
    assert p.must["water_rating"] == (">=", 7)
    assert p.os == ["android"] and p.exclude_brands == ["samsung"] and p.brands == ["xiaomi"]
    assert "not Samsung" in p.understood


def test_iphone_sets_os_not_brand_lock():
    p = parse_query("iphone under 1.5 lakh")
    assert p.category == Category.PHONE and p.os == ["ios"] and p.brands == []


def test_os_upgrades_parsing():
    assert parse_os_upgrades("Android 14, up to 7 major Android upgrades") == 7
    assert parse_os_upgrades("5 years of OS updates") == 5
    assert parse_os_upgrades("Android 15, One UI 7") is None
