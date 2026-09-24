"""raw -> clean -> refine -> index."""

import json
from pathlib import Path

from scrapling.parser import Selector

from devicescout.clean import clean
from devicescout.models import Category, Offer, Product
from devicescout.pipeline import ingest, reprocess
from devicescout.sources import GSMArenaSource, ShopifySource
from devicescout.storage import Store, resolve_spec

FIX = Path(__file__).parent / "fixtures"


def phone(**kw):
    base = dict(source="shop", url="https://shop/x", name="Koshi K5 5G", brand="Koshi", category=Category.PHONE,
                specs={"battery_mah": 5000}, offers=[Offer("shop", "https://shop/x", 45000, "NPR")])
    base.update(kw)
    return Product(**base)


def test_clean_rejects_junk_and_impossible_values():
    p, issues = clean(phone(name="Koshi K5 &amp; Case", specs={"battery_mah": 20000, "weight_g": 1000, "ram_gb": 8},
                            offers=[Offer("s", "u", 45000, "NPR"), Offer("s", "u2", 1999, "NPR"),
                                    Offer("s", "u3", 44000, "NPR", original_price=440000)]))
    assert p.name == "Koshi K5 & Case"
    assert p.specs == {"ram_gb": 8}                                  # power-bank battery and 1 kg dropped
    assert [o.price for o in p.offers] == [45000, 44000]             # Rs 1,999 phone = EMI/placeholder
    assert p.offers[1].original_price is None                        # 10x "discount" is not a discount
    kinds = sorted(i.kind for i in issues)
    assert kinds.count("dropped_spec") == 2 and "dropped_offer" in kinds and "fixed" in kinds
    assert clean(phone(name="N/A"))[0] is None


def test_resolve_spec_priority_then_agreement():
    assert resolve_spec({"gsmarena": 5000, "shop": 4900}) == (5000, "gsmarena", False)
    value, source, conflict = resolve_spec({"shop-a": 4000, "shop-b": 5000, "shop-c": 5010})
    assert (value, conflict) == (5000, True) and source == "shop-b"  # two sources agree on ~5000


def test_ingest_keeps_raw_dedups_and_logs(tmp_path):
    s = Store(tmp_path / "t.db")
    ingest(s, phone())
    ingest(s, phone())                                    # same content: raw layer not duplicated
    ingest(s, phone(name="x"))                            # rejected, still kept raw for later reprocess
    assert s.quality_summary()["raw_records"] == 2
    assert s.quality_summary()["by_kind"]["rejected"] == 1
    assert len(s.products()) == 1


def test_conflicts_between_sources_are_logged(tmp_path):
    s = Store(tmp_path / "t.db")
    ingest(s, phone(source="a", specs={"battery_mah": 5000}))
    ingest(s, phone(source="b", specs={"battery_mah": 4000}))
    assert s.quality_summary()["by_kind"].get("conflict") == 1


def test_full_text_search_uses_aliases_and_chipset(tmp_path):
    s = Store(tmp_path / "t.db")
    ingest(s, GSMArenaSource().parse(Selector((FIX / "gsmarena_s24_ultra.html").read_text(), url="https://g/s24")))
    shop = ShopifySource(name="brother-mart", base_url="https://brother-mart.com")
    ingest(s, shop.parse_product(json.loads((FIX / "shopify_products.json").read_text())["products"][0], "phones"))
    assert s.search("s24 ultra") == ["samsung galaxy s24 ultra"]
    assert s.search("snapdragon 8") == ["samsung galaxy s24 ultra"]          # chipset column
    assert set(s.search("galaxy")) == {"samsung galaxy s24 ultra", "samsung galaxy a56"}
    assert s.search("exynos", Category.PHONE) == ["samsung galaxy a56"]
    assert s.search("a56 5g") == ["samsung galaxy a56"]                      # alias keeps "5G"


def test_reprocess_rebuilds_from_raw(tmp_path):
    s = Store(tmp_path / "t.db")
    p = GSMArenaSource().parse(Selector((FIX / "gsmarena_s24_ultra.html").read_text(), url="https://g/s24"))
    ingest(s, p)
    s.db.execute("UPDATE products SET specs = '{}'")        # simulate a catalogue built by older code
    s.db.commit()
    stats = reprocess(s)
    assert stats.stored == 1
    [again] = s.products()
    assert again.specs["battery_mah"] == 5000 and again.specs["os_upgrades"] == 7
    assert s.search("s24") == [again.key]


def test_variant_specs_are_not_logged_as_conflicts(tmp_path):
    """gsmarena=8 GB vs gadgetbyte=6 GB RAM is two variants of one phone, not an error;
    a 4.1" vs 6.9" screen still is worth a look."""
    s = Store(tmp_path / "x.db")
    s.upsert(phone(source="gsmarena", specs={"ram_gb": 8, "storage_gb": 256, "display_size_in": 4.1}))
    s.upsert(phone(source="gadgetbyte", specs={"ram_gb": 6, "storage_gb": 128, "display_size_in": 6.9}))
    fields = {r["field"] for r in s.quality_summary()["top"] if r["kind"] == "conflict"}
    assert fields == {"display_size_in"}


def test_source_status_comes_from_the_newer_of_check_and_update():
    from devicescout.server import _health
    fresh = _health({"last_scraped_at": "2026-09-24T12:00:00+00:00", "last_scrape_count": 167})
    assert fresh["check"] == "OK" and "167 items" in fresh["check_detail"]
    empty = _health({"last_scraped_at": "2026-09-24T12:00:00+00:00", "last_scrape_count": 0,
                     "check": "OK", "checked_at": "2026-09-23T12:00:00+00:00"})
    assert empty["check"] == "FAIL"                        # a newer empty update beats an old passing check
    later_check = {"last_scraped_at": "2026-09-23T12:00:00+00:00", "last_scrape_count": 0,
                   "check": "OK", "checked_at": "2026-09-24T12:00:00+00:00", "check_detail": "3 products"}
    assert _health(later_check) == later_check
    assert _health({"check": "RUNNING", "last_scraped_at": "x"})["check"] == "RUNNING"


def test_restart_clears_updating_and_checking_flags():
    from devicescout.jobs import _save_status, clear_interrupted, load_status
    _save_status("gadgetbyte", scrape_running=True, last_scrape_count=87)
    _save_status("itti", check="RUNNING")
    clear_interrupted()
    st = load_status()
    assert st["gadgetbyte"] == {"scrape_running": False, "last_scrape_count": 87} and "check" not in st["itti"]
