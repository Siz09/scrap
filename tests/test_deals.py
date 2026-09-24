"""Deals are judged against the market and history, not the store's crossed-out price."""

from datetime import datetime, timedelta, timezone

from devicescout.deals import evaluate
from devicescout.models import Category, Offer, Product

NOW = datetime.now(timezone.utc)


def at(days_ago):
    return (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")


def product(*offers):
    return Product(source="t", url="u", name="Koshi K5", category=Category.PHONE, offers=list(offers))


def offer(seller, price, original=None, url=None):
    return Offer(seller, url or f"https://{seller}", price, "NPR", scraped_at=at(0), seller=seller,
                 original_price=original)


def verdicts(deals, seller):
    return next(d.verdicts for d in deals if d.offer.seller == seller)


def test_real_deal_against_other_sellers():
    deals = evaluate(product(offer("a", 50000, 60000), offer("b", 60000), offer("c", 61000)), [])
    assert [d.offer.seller for d in deals] == ["a"]
    d = deals[0]
    assert d.market_price == 60500 and d.saving == 10500 and "real deal" in d.verdicts and d.verified


def test_cheapest_seller_by_a_little_is_not_a_deal():
    assert evaluate(product(offer("a", 58000), offer("b", 60000), offer("c", 61000)), []) == []


def test_paper_discount_and_inflated_original():
    deals = evaluate(product(offer("a", 60000, 90000), offer("b", 59000), offer("c", 60500)), [])
    v = verdicts(deals, "a")
    assert "paper discount" in v and "inflated original" in v and not deals[0].verified


def test_price_drop_and_lowest_seen_from_history():
    history = [(at(40), 60000, "https://a"), (at(20), 60000, "https://a"), (at(10), 59500, "https://b")]
    deals = evaluate(product(offer("a", 54000)), history)
    v = verdicts(deals, "a")
    assert "price drop" in v and "lowest price seen" in v and deals[0].dropped_from == 60000


def test_claim_with_nothing_to_compare_is_unverified():
    deals = evaluate(product(offer("a", 50000, 60000)), [])
    assert verdicts(deals, "a") == ["unverified"] and not deals[0].verified
