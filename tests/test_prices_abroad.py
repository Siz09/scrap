"""Devices not sold in Nepal: live exchange rates, converted prices, availability."""

import json

from devicescout import currency
from devicescout.advisor import Needs, advise
from devicescout.jobs import refresh_rates
from devicescout.models import Category, Offer, Product
from devicescout.normalize import parse_foreign_price
from devicescout.storage import Store

NRB = {"data": {"payload": [
    {"date": "2026-09-23", "rates": [{"currency": {"iso3": "USD", "unit": 1}, "buy": "140.00", "sell": "140.60"}]},
    {"date": "2026-09-24", "rates": [
        {"currency": {"iso3": "USD", "unit": 1}, "buy": "141.00", "sell": "141.60"},
        {"currency": {"iso3": "INR", "unit": 100}, "buy": "160.00", "sell": "160.15"},
        {"currency": {"iso3": "JPY", "unit": 10}, "buy": "9.40", "sell": "9.50"}]}]}}


def test_rates_from_nepal_rastra_bank(monkeypatch):
    monkeypatch.setattr(currency, "_get_json", lambda url, timeout=15: NRB)
    info = currency.fetch_rates()
    assert info["source"] == "Nepal Rastra Bank" and info["date"] == "2026-09-24"     # the latest day
    assert currency.RATES_TO_NPR["USD"] == 141.3 and currency.RATES_TO_NPR["JPY"] == 0.945
    assert currency.RATES_TO_NPR["INR"] == 1.6                                        # the peg
    assert currency.to_npr(299, "USD") == round(299 * 141.3, 2)


def test_backup_feed_when_nrb_is_down(monkeypatch):
    def fake(url, timeout=15):
        if "nrb.org.np" in url:
            raise OSError("down")
        return {"rates": {"USD": 1 / 142.0, "EUR": 1 / 155.0}, "time_last_update_utc": "Thu, 24 Sep 2026 00:00"}
    monkeypatch.setattr(currency, "_get_json", fake)
    assert currency.fetch_rates()["source"] == "open.er-api.com"
    assert currency.RATES_TO_NPR["USD"] == 142.0


def test_scrape_saves_rates_for_the_website_and_falls_back_to_the_last_ones(tmp_path, monkeypatch):
    store = Store(tmp_path / "x.db")
    monkeypatch.setattr(currency, "_get_json", lambda url, timeout=15: NRB)
    lines = []
    refresh_rates(store, lines.append)
    assert json.loads(store.get_kv("fx_rates"))["rates"]["USD"] == 141.3
    assert "1 USD = Rs 141.3" in lines[0]

    def offline(url, timeout=15):
        raise OSError("offline")
    monkeypatch.setattr(currency, "_get_json", offline)
    currency.RATES_TO_NPR["USD"] = 99.0
    refresh_rates(store, lines.append)
    assert currency.RATES_TO_NPR["USD"] == 141.3 and "last saved rates" in lines[-1]


def test_foreign_price_text():
    assert parse_foreign_price("$ 299.99 / € 279.00 / £ 249.00 / ₹ 24,999") == (299.99, "USD")
    assert parse_foreign_price("About 250 EUR") == (250.0, "EUR")
    assert parse_foreign_price("₹ 1,29,999") == (129999.0, "INR")
    assert parse_foreign_price("Coming soon") is None


def phone(name, local=None, abroad=None, battery=5000):
    offers = []
    if local:
        offers.append(Offer("shop", f"https://shop/{name}", local, "NPR", region="np", seller="Shop"))
    if abroad:
        offers.append(Offer("gsmarena", f"https://gsmarena/{name}", abroad, "USD", region="intl",
                            seller="GSMArena (market price abroad)"))
    return Product(source="t", url=f"https://t/{name}", name=name, brand=name.split()[0], category=Category.PHONE,
                   specs={"battery_mah": battery, "os": "android"}, offers=offers, key=name.lower())


def test_devices_not_sold_in_nepal_are_shown_with_a_converted_price():
    products = [phone("Koshi Local", local=45000), phone("Nimbus Abroad", abroad=300, battery=6000),
                phone("Terai Nothing", battery=5500)]
    a = advise(products, Needs(category=Category.PHONE, uses={"battery": 1}))
    got = {p.ranked.product.name: p for p in a.picks}
    assert set(got) == {"Koshi Local", "Nimbus Abroad", "Terai Nothing"}       # nobody hidden
    abroad = got["Nimbus Abroad"].to_dict()
    assert abroad["price_converted"] and not abroad["available_in_nepal"]
    assert abroad["price_npr"] == currency.to_npr(300, "USD")
    assert abroad["converted_from"] == {"price": 300, "currency": "USD", "seller": "GSMArena (market price abroad)",
                                       "url": "https://gsmarena/Nimbus Abroad"}
    assert abroad["where_to_buy"][0]["converted"] is True
    local = got["Koshi Local"].to_dict()
    assert local["available_in_nepal"] and not local["price_converted"]
    assert got["Terai Nothing"].to_dict()["price_npr"] is None                  # no price anywhere, still shown


def test_budget_uses_converted_prices_and_nepal_only_hides_imports():
    products = [phone("Koshi Local", local=45000), phone("Nimbus Abroad", abroad=300), phone("Terai Nothing")]
    a = advise(products, Needs(category=Category.PHONE, budget_max=50000))
    assert {p.ranked.product.name for p in a.picks} == {"Koshi Local", "Nimbus Abroad"}   # $300 ≈ Rs 42,000
    assert a.excluded["no_price"] == 1                  # can't tell if a price-less phone fits a budget
    a = advise(products, Needs(category=Category.PHONE, nepal_only=True))
    assert [p.ranked.product.name for p in a.picks] == ["Koshi Local"] and a.excluded["no_nepal_price"] == 2
