"""Crawl loops and platform detection, driven by a fake fetcher (no network)."""

import json
from pathlib import Path

from scrapling.parser import Selector

from devicescout.sources import DarazSource, ShopifySource, WooCommerceSource
from devicescout.sources.detect import detect

FIX = Path(__file__).parent / "fixtures"


class FakePage(Selector):
    def __init__(self, body: str, url: str, status: int = 200):
        super().__init__(body, url=url)
        self.status = status  # .body comes from Selector (raw text), like a live Response


class FakeFetcher:
    """Routes URLs to canned responses by substring; everything else 404s."""

    def __init__(self, routes: dict[str, object]):
        self.routes, self.calls = routes, []

    def _match(self, url):
        self.calls.append(url)
        for needle, resp in self.routes.items():
            if needle in url:
                return resp(url) if callable(resp) else resp
        raise RuntimeError(f"HTTP 404 for {url}")

    def get(self, url, headers=None, mode=None):
        r = self._match(url)
        return FakePage(r if isinstance(r, str) else json.dumps(r), url)

    def get_json(self, url, headers=None):
        r = self._match(url)
        if isinstance(r, str):
            raise ValueError("expected JSON, got HTML")
        return r


def fixture(name):
    return json.loads((FIX / name).read_text())


def test_daraz_crawl_pages_and_dedupes():
    catalog = fixture("daraz_catalog.json")
    f = FakeFetcher({"page=1": catalog, "page=2": catalog, "page=3": {"mods": {}}, "daraz.com.np/": "<html></html>"})
    src = DarazSource(categories=["mobile-phones"], queries=[], pages=3, max_price=60000)
    products = list(src.crawl(f, limit=100))
    assert len(products) == 6                          # page 2 repeats page 1 -> deduped
    assert any("price=0-60000" in u for u in f.calls)  # budget pushed to Daraz server-side


def test_shopify_crawl_stops_at_empty_page():
    f = FakeFetcher({"page=1": fixture("shopify_products.json"), "page=2": {"products": []}})
    src = ShopifySource(name="shop", base_url="https://shop.com.np", collections=["phones"], max_pages=5)
    products = list(src.crawl(f))
    assert [p.name for p in products] == ["Samsung Galaxy A56 5G", "Spigen Tough Armor Case for Galaxy A56"]
    assert all("/collections/phones/products.json" in u for u in f.calls)


def test_woocommerce_resolves_category_slugs():
    f = FakeFetcher({
        "/products/categories": [{"id": 7, "slug": "laptops"}, {"id": 9, "slug": "mobiles"}],
        "page=1&category=7": fixture("woo_products.json"),
        "page=2&category=7": [],
    })
    src = WooCommerceSource(name="w", base_url="https://w.com.np", categories=["laptops", "nope"])
    products = list(src.crawl(f))
    assert len(products) == 1 and products[0].specs["ram_gb"] == 16


def test_detect_order():
    shopify = FakeFetcher({"/products.json": {"products": []}})
    assert detect(shopify, "https://a.com.np")["platform"] == "shopify"
    woo = FakeFetcher({"/wp-json/wc/store/v1/products": []})
    assert detect(woo, "https://b.com.np")["platform"] == "woocommerce"
    assert detect(FakeFetcher({}), "https://www.daraz.com.np")["platform"] == "daraz"
    product_html = (FIX / "store_pixel9.html").read_text()
    jsonld = FakeFetcher({
        "robots.txt": "User-agent: *\nSitemap: https://c.com.np/sitemap_index.xml",
        "sitemap_index.xml": "<sitemapindex><sitemap><loc>https://c.com.np/product-sitemap.xml</loc></sitemap></sitemapindex>",
        "product-sitemap.xml": "<urlset><url><loc>https://c.com.np/product/pixel-9/</loc></url></urlset>",
        "/product/pixel-9/": product_html,
    })
    r = detect(jsonld, "https://c.com.np")
    assert r["platform"] == "jsonld" and "pixel-9" in r["evidence"]
    assert detect(FakeFetcher({}), "https://d.com.np")["platform"] == "unknown"


class _Resp:
    def __init__(self, status, body=b""):
        self.status, self.body = status, body


class _Session:
    def __init__(self, resp):
        self.resp = resp

    def get(self, url, **kw):
        return self.resp


def test_robots_rules_respected_but_bot_wall_does_not_block_everything():
    from devicescout.sources.base import Fetcher
    f = Fetcher(delay=0)
    f._session = _Session(_Resp(200, b"User-agent: *\nDisallow: /checkout\n"))
    assert f.allowed("https://shop.com.np/product/x") and not f.allowed("https://shop.com.np/checkout/1")
    g = Fetcher(delay=0)
    g._session = _Session(_Resp(403))
    assert g.allowed("https://walled.com.np/product/x")


def test_cli_scrape_check_and_advise(tmp_path, monkeypatch, capsys):
    from devicescout import cli

    catalog = fixture("daraz_catalog.json")

    class CtxFetcher(FakeFetcher):
        def __init__(self, *a, **k):
            super().__init__({"ajax=true": catalog, "daraz.com.np/": "<html></html>"})

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

    monkeypatch.setattr(cli, "Fetcher", CtxFetcher)
    reg = tmp_path / "sources.json"
    reg.write_text(json.dumps({"sources": [{"name": "daraz-np", "type": "daraz", "categories": ["mobile-phones"],
                                            "queries": [], "pages": 1}]}))
    db = str(tmp_path / "d.db")
    cli.main(["--db", db, "--sources", str(reg), "check"])
    assert "daraz-np        OK" in capsys.readouterr().out
    cli.main(["--db", db, "--sources", str(reg), "scrape", "--all"])
    assert "daraz-np: 6 records: 6 stored" in capsys.readouterr().out
    cli.main(["--db", db, "advise", "--category", "phone", "--budget", "50k", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["picks"][0]["name"].startswith("Redmi Note 14 Pro")
    assert out["picks"][0]["price_npr"] == 38999
