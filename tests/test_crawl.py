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

    def get(self, url, headers=None, mode=None, want_json=False, fallback=True, scroll=False):
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
    assert "https://shop.com.np/products.json?limit=250&page=1" in f.calls   # the whole store first
    assert not any("page=2" in u for u in f.calls)       # a short page is the last one: no extra requests


def test_shopify_stops_when_the_store_repeats_the_same_page():
    """brother-mart: every collection was read to page 10 because pages kept coming back full."""
    shop = fixture("shopify_products.json")
    f = FakeFetcher({"products.json": shop})           # every page returns the same products
    src = ShopifySource(name="shop", base_url="https://shop.com.np", max_pages=50)
    src.PAGE = len(shop["products"])                    # so each page looks "full"
    assert len(list(src.crawl(f))) == len(shop["products"])
    assert sum("products.json" in u for u in f.calls) == 2   # page 1, then page 2 = same ids: stop


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
    from devicescout.storage import Store
    assert Store(db).stats()["raw_records"] == 3       # a check keeps the sample it pulled
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
        def get(self, url, headers=None, mode=None, want_json=False, fallback=True, scroll=False):
            self.calls.append((url, mode))
            return FakePage(f"<html><body>{rendered if mode == 'dynamic' else nav}</body></html>", url)

    f = BrowserFetcher({})
    src = GenericSource(SiteConfig(name="s", base_url="https://shop.com.np", start_urls=["https://shop.com.np/mobile-phones"]))
    urls = list(src._browse(f, "https://shop.com.np"))
    assert urls == ["https://shop.com.np/samsung-galaxy-s25-ultra-12gb-256gb", "https://shop.com.np/redmi-note-14-pro-5g"]
    assert ("https://shop.com.np/mobile-phones", "dynamic") in f.calls
    assert src.listing_mode == "dynamic"     # later listings go straight to the browser
    assert src.fetch_mode == "static"        # product pages are still tried plain first (hukut: much faster)


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


def test_site_crawl_walks_every_listing_page_and_follows_product_links():
    """No sitemap: categories from the menu, page 2, and related products on product pages are all found;
    the listing a product was found on tells its category."""
    from devicescout.sources import GenericSource, SiteConfig

    def product(name, price, related=""):
        return (f'<html><head><script type="application/ld+json">{{"@type": "Product", "name": "{name}", '
                f'"offers": {{"price": "{price}", "priceCurrency": "NPR"}}}}</script></head>'
                f'<body><h1>{name}</h1>{related}</body></html>')

    menu = '<a href="/mobile-phones">Phones</a><a href="/laptops">Laptops</a><a href="/tv">TV</a><a href="/cart">c</a>'
    routes = {
        "shop.com.np/mobile-phones?page=2": f'<html><body>{menu}<a href="/redmi-note-14-pro-5g">R</a></body></html>',
        "shop.com.np/mobile-phones": f'<html><body>{menu}<a href="/samsung-galaxy-a56-5g?ref=x">A56</a>'
                                     f'<a href="/mobile-phones?page=2&sort=price">2</a>'
                                     f'<a href="/mobile-phones?sort=new">sorted</a></body></html>',
        "shop.com.np/laptops": f'<html><body>{menu}<a href="/hp-15s-i5-16gb-512gb">HP</a></body></html>',
        "shop.com.np/tv": f'<html><body>{menu}<a href="/mi-43-inch-a-series-2025">TV</a></body></html>',
        "shop.com.np/samsung-galaxy-a56-5g": product("Samsung Galaxy A56 5G", 54999,
                                                     '<a href="/samsung-galaxy-a36-5g-8gb">related</a>'),
        "shop.com.np/samsung-galaxy-a36-5g-8gb": product("Samsung Galaxy A36 5G", 42999),
        "shop.com.np/redmi-note-14-pro-5g": product("Redmi Note 14 Pro 5G", 39999),
        "shop.com.np/hp-15s-i5-16gb-512gb": product("HP 15s 16GB 512GB", 89999),
        "shop.com.np/mi-43-inch-a-series-2025": product("Mi 43 inch A Series 2025", 45999),
    }
    home = f"<html><body>{menu}</body></html>"

    class SiteFetcher(FakeFetcher):
        def _match(self, url):
            self.calls.append(url)
            if url.rstrip("/") == "https://shop.com.np":
                return home
            for needle in sorted(routes, key=len, reverse=True):      # most specific route first
                if needle in url and not ("?" in url and "?" not in needle and "page=" in url):
                    return routes[needle]
            raise RuntimeError(f"HTTP 404 for {url}")

    f = SiteFetcher({})
    src = GenericSource(SiteConfig(name="shop", base_url="https://shop.com.np"))
    got = {p.name: p.category.value for p in src.crawl(f, limit=100)}
    assert got == {"Samsung Galaxy A56 5G": "phone", "Samsung Galaxy A36 5G": "phone",
                   "Redmi Note 14 Pro 5G": "phone", "HP 15s 16GB 512GB": "laptop",
                   "Mi 43 inch A Series 2025": "tv"}
    assert "https://shop.com.np/mobile-phones?page=2" in f.calls
    assert not any("sort=" in u for u in f.calls)                 # sort/filter variants aren't re-walked
    assert f.calls.count("https://shop.com.np/samsung-galaxy-a56-5g") == 1


def test_site_crawl_stops_at_listing_budget():
    from devicescout.sources import GenericSource, SiteConfig

    class Endless(FakeFetcher):
        def get(self, url, headers=None, mode=None, want_json=False, fallback=True, scroll=False):
            self.calls.append(url)
            n = int(url.rsplit("=", 1)[1]) if "page=" in url else 1
            return FakePage(f'<html><body><a href="/deals?page={n + 1}">next</a>'
                            f'<a href="/phones">p</a><a href="/about-us">a</a></body></html>', url)

    f = Endless({})
    src = GenericSource(SiteConfig(name="s", base_url="https://shop.com.np", browse_pages=5))
    assert list(src.crawl(f, limit=100)) == []
    listing_fetches = [u for u in f.calls if not u.endswith((".xml", "robots.txt"))]
    assert len(set(listing_fetches)) <= 5


def test_sources_scrape_top_to_bottom_and_recent_checks_are_not_repeated():
    from devicescout.jobs import _save_status, recently_checked, scrape_order
    from devicescout.sources.detect import remember

    remember("hukut", {"platform": "unknown"})
    remember("brother-mart", {"platform": "shopify"})
    entries = [{"name": "gsmarena", "type": "gsmarena", "role": "specs"}, {"name": "hukut"},
               {"name": "gadgetbyte", "type": "jsonld", "role": "reference"}, {"name": "brother-mart"},
               {"name": "never-checked-store"}]
    assert [e["name"] for e in scrape_order(entries)] == [
        "gsmarena", "hukut", "gadgetbyte", "brother-mart", "never-checked-store"]

    assert not recently_checked(entries)
    for e in entries:
        _save_status(e["name"], checked_at="2020-01-01T00:00:00+00:00")
    assert not recently_checked(entries)
    from devicescout.jobs import _now
    for e in entries:
        _save_status(e["name"], checked_at=_now())
    assert recently_checked(entries)


def test_product_group_variants_become_separate_prices():
    """hukut.com product pages: schema.org ProductGroup with one Product per storage option."""
    from devicescout.sources import GenericSource, SiteConfig

    ld = {"@context": "https://schema.org", "@type": "ProductGroup", "name": "Samsung Galaxy A57",
          "brand": {"@type": "Brand", "name": "Samsung"},
          "hasVariant": [
              {"@type": "Product", "name": "Samsung Galaxy A57 8GB/128GB",
               "offers": {"@type": "Offer", "price": 54999, "priceCurrency": "NPR",
                          "availability": "https://schema.org/InStock"}},
              {"@type": "Product", "name": "Samsung Galaxy A57 8GB/256GB",
               "offers": {"@type": "Offer", "price": 59999, "priceCurrency": "NPR",
                          "availability": "https://schema.org/OutOfStock"}}]}
    html = (f'<html><head><script type="application/ld+json">{json.dumps(ld)}</script></head>'
            f'<body><h1>Samsung Galaxy A57</h1><p>Rs. 4,500 off</p></body></html>')
    p = GenericSource(SiteConfig(name="hukut", base_url="https://hukut.com")).parse(
        FakePage(html, "https://hukut.com/samsung-galaxy-a57"))
    assert p.name == "Samsung Galaxy A57" and p.brand == "Samsung" and p.category.value == "phone"
    assert [(o.variant, o.price, o.in_stock) for o in p.offers] == [("8/128", 54999, True), ("8/256", 59999, False)]


def test_js_shell_and_non_product_pages_are_recognised():
    from devicescout.sources import GenericSource, SiteConfig
    from devicescout.sources.base import _looks_like_js_shell

    menu = "Laptops Desktops Gaming Monitors " * 18                       # ~570 characters of menu text
    itti_like = FakePage(f"<html><body>{menu}<script>{'x' * 60000}</script></body></html>", "https://itti.com.np/")
    assert _looks_like_js_shell(itti_like)
    article = FakePage(f"<html><body>{'A real article paragraph. ' * 120}</body></html>", "https://x.com/a")
    assert not _looks_like_js_shell(article)

    src = GenericSource(SiteConfig(name="itti", base_url="https://itti.com.np"))
    assert src._looks_like_product("https://itti.com.np/product/asus-zenbook-14-um3406ga-price-nepal")
    assert not src._looks_like_product("https://itti.com.np/about-itti-pvt-ltd")
    assert not src._looks_like_product("https://itti.com.np/itti-terms-and-conditions")


def test_sitemap_of_category_pages_seeds_the_site_walk():
    """itti.com.np: the sitemap lists category pages; products live under /product/..."""
    from devicescout.sources import GenericSource, SiteConfig

    sitemap = ('<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
               '<url><loc>https://itti.com.np/laptops-by-brands/asus-laptop-nepal</loc></url>'
               '<url><loc>https://itti.com.np/laptops-by-brands/asus-laptop-nepal/zenbook-series</loc></url></urlset>')
    ld = json.dumps({"@type": "Product", "name": "ASUS Zenbook 14 UM3406GA",
                     "offers": {"price": "154999", "priceCurrency": "NPR"}})
    product = f'<html><head><script type="application/ld+json">{ld}</script></head><body><h1>x</h1></body></html>'
    routes = {
        "itti.com.np/sitemap.xml": sitemap,
        "itti.com.np/laptops-by-brands/asus-laptop-nepal/zenbook-series":
            '<html><body><h1>Laptop price in Nepal 2026</h1>'
            '<a href="/product/asus-zenbook-14-um3406ga-price-nepal">Zenbook</a>'
            '<a href="/about-itti-pvt-ltd">About</a></body></html>',
        "itti.com.np/laptops-by-brands/asus-laptop-nepal": '<html><body><h1>ASUS</h1>'
            '<p>Processor: Ryzen 7</p><p>RAM: 16GB</p><p>Storage: 1TB SSD</p></body></html>',
        "itti.com.np/product/asus-zenbook-14-um3406ga-price-nepal": product,
    }

    class Itti(FakeFetcher):
        def _match(self, url):
            self.calls.append(url)
            for needle in sorted(routes, key=len, reverse=True):
                if url.rstrip("/").endswith(needle):
                    return routes[needle]
            if url.rstrip("/") == "https://itti.com.np":
                return "<html><body></body></html>"
            raise RuntimeError(f"HTTP 404 for {url}")

    f = Itti({})
    got = list(GenericSource(SiteConfig(name="itti", base_url="https://itti.com.np")).crawl(f, limit=10))
    assert [(p.name, p.offers[0].price, p.category.value) for p in got] == [
        ("ASUS Zenbook 14 UM3406GA", 154999, "laptop")]
    assert "https://itti.com.np/about-itti-pvt-ltd" not in f.calls


def test_category_pages_with_long_slugs_are_not_products():
    """itti.com.np: /laptops-by-brands/dell/dell-precision-pro-max-laptops became a 'product'
    ('DellPrecision/ProMaxLaptopsPriceinNepal') with a price taken from one of its cards."""
    from devicescout.sources import GenericSource, SiteConfig

    src = GenericSource(SiteConfig(name="itti", base_url="https://itti.com.np"))
    for url in ("https://itti.com.np/laptops-by-brands/dell/dell-precision-pro-max-laptops",
                "https://itti.com.np/gadgets/mobiles/blackview-smartphones-price-nepal"):
        assert not src._looks_like_product(url), url
    for url in ("https://itti.com.np/product/acer-nitro-vg271u-gaming-monitor-price-nepal",
                "https://shop.com.np/mi-43-inch-a-series-2025", "https://hukut.com/samsung-galaxy-a57"):
        assert src._looks_like_product(url), url

    cards = "".join(f'<a href="/product/dell-pro-{i}-laptop-16gb">Dell {i}</a><p>Rs. {90000 + i},000</p>'
                    for i in range(8))
    listing = FakePage(f"<html><body><h1>Dell Laptops Price in Nepal</h1>{cards}</body></html>",
                       "https://itti.com.np/some-dell-laptop-range-2026")
    assert src._is_listing(listing)
    assert src._product(FakeFetcher({}), listing.url, listing) is None
