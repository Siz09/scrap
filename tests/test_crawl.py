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

    def get(self, url, headers=None, mode=None, want_json=False, fallback=True):
        r = self._match(url)
        return FakePage(r if isinstance(r, str) else json.dumps(r), url)

    def get_json(self, url, headers=None, fallback=True):
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
    assert all("/collections/phones/products.json" in u or "/collections.json" in u for u in f.calls)


def test_shopify_also_crawls_sale_and_festival_collections():
    shop = fixture("shopify_products.json")
    f = FakeFetcher({
        "/collections.json": {"collections": [{"handle": "phones", "title": "Phones"},
                                              {"handle": "dashain-offer", "title": "Dashain Offer"},
                                              {"handle": "cables", "title": "Cables"}]},
        "/collections/phones/products.json?limit=250&page=1": {"products": shop["products"][:1]},
        "/collections/dashain-offer/products.json?limit=250&page=1": {"products": shop["products"][1:]},
        "page=2": {"products": []},
    })
    src = ShopifySource(name="shop", base_url="https://shop.com.np", collections=["phones"], max_pages=2)
    assert len(list(src.crawl(f))) == 2
    assert any("dashain-offer" in u for u in f.calls) and not any("cables" in u for u in f.calls)


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


def test_browses_category_pages_when_no_sitemap():
    from devicescout.sources import GenericSource, SiteConfig

    links = "".join(f'<a href="/{s}">x</a>' for s in
                    ["samsung-galaxy-a56-5g-8gb-256gb", "redmi-note-14-pro-5g", "cart", "about-us",
                     "mobile-phones", "logo.png", "apple-iphone-16-pro-max-256gb"])
    home = '<html><body><a href="/mobile-phones">Phones</a><a href="/laptops">Laptops</a>' + "x" * 500 + "</body></html>"
    f = FakeFetcher({
        "shop.com.np/mobile-phones": f"<html><body>{links}{'x' * 500}</body></html>",
        "shop.com.np/laptops": "<html><body>" + "x" * 500 + "</body></html>",
        "https://shop.com.np/": home,
    })
    urls = list(GenericSource(SiteConfig(name="s", base_url="https://shop.com.np/"))._browse(f, "https://shop.com.np/"))
    assert urls == ["https://shop.com.np/samsung-galaxy-a56-5g-8gb-256gb",
                    "https://shop.com.np/redmi-note-14-pro-5g",
                    "https://shop.com.np/apple-iphone-16-pro-max-256gb"]


def test_page_without_product_data_is_not_a_product():
    from devicescout.sources import GenericSource, SiteConfig
    page = Selector("<html><body><h1>Latest mobile news</h1><p>text</p></body></html>", url="https://x/news")
    assert GenericSource(SiteConfig(name="s")).parse(page) is None


def test_js_built_category_page_is_rendered_in_a_browser():
    """Like hukut.com: the plain page has only menu links; product cards appear after JavaScript runs."""
    from devicescout.sources import GenericSource, SiteConfig

    nav = "".join(f'<a href="/{s}">x</a>' for s in ["laptops", "cart", "about-us", "brands", "offers", "support"])
    rendered = nav + '<a href="/samsung-galaxy-s25-ultra-12gb-256gb">S25</a><a href="/redmi-note-14-pro-5g">R</a>'

    class BrowserFetcher(FakeFetcher):
        def get(self, url, headers=None, mode=None, want_json=False, fallback=True):
            self.calls.append((url, mode))
            return FakePage(f"<html><body>{rendered if mode == 'dynamic' else nav}</body></html>", url)

    f = BrowserFetcher({})
    src = GenericSource(SiteConfig(name="s", base_url="https://shop.com.np", start_urls=["https://shop.com.np/mobile-phones"]))
    urls = list(src._browse(f, "https://shop.com.np"))
    assert urls == ["https://shop.com.np/samsung-galaxy-s25-ultra-12gb-256gb", "https://shop.com.np/redmi-note-14-pro-5g"]
    assert ("https://shop.com.np/mobile-phones", "dynamic") in f.calls
    assert src.fetch_mode == "dynamic"       # its product pages will be rendered too


def test_product_links_read_from_embedded_page_data():
    from devicescout.sources import GenericSource, SiteConfig

    data = {"props": {"pageProps": {"products": [
        {"name": "Galaxy A56", "slug": "samsung-galaxy-a56-5g-8gb-256gb", "price": 54999},
        {"name": "iPhone 16", "url": "/apple-iphone-16-128gb", "image": {"url": "/img/iphone-16-front-1.webp"}},
    ]}}}
    page = f'<html><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></body></html>'
    f = FakeFetcher({"shop.com.np/mobile-phones": page})
    src = GenericSource(SiteConfig(name="s", base_url="https://shop.com.np", start_urls=["https://shop.com.np/mobile-phones"]))
    assert list(src._browse(f, "https://shop.com.np")) == ["https://shop.com.np/apple-iphone-16-128gb",
                                                            "https://shop.com.np/samsung-galaxy-a56-5g-8gb-256gb"]
