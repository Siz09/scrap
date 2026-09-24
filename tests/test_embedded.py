"""Stores built with JavaScript frameworks, and keeping users' source lists current."""

import json

from scrapling.parser import Selector

from devicescout.sources import GenericSource, SiteConfig

NEXT_PAGE = """<html><head><meta property="og:title" content="Samsung Galaxy A56 5G | Store">
<script id="__NEXT_DATA__" type="application/json">{"props": {"pageProps": {
  "related": [{"name": "Galaxy A16 Case", "price": 999}],
  "product": {"name": "Samsung Galaxy A56 5G (8GB/256GB)", "brand": {"name": "Samsung"},
              "sellingPrice": 57999, "mrp": 61999,
              "specifications": [{"key": "Battery", "value": "5000 mAh"}, {"key": "RAM", "value": "8GB"},
                                 {"key": "Display", "value": "6.7 inch 120Hz"}]}}}}</script></head>
<body><div id="__next"><h1>Samsung Galaxy A56 5G</h1></div></body></html>"""


def test_nextjs_store_product_page():
    p = GenericSource(SiteConfig(name="hukut", region="np")).parse(Selector(NEXT_PAGE, url="https://hukut.com/a56"))
    assert p.name == "Samsung Galaxy A56 5G" and p.brand == "Samsung"
    o = p.offers[0]
    assert (o.price, o.original_price, o.currency) == (57999, 61999, "NPR")    # not the related case's 999
    assert p.specs["battery_mah"] == 5000 and p.specs["ram_gb"] == 8 and p.specs["refresh_rate_hz"] == 120


def test_meta_tag_price():
    html = """<html><head><meta property="product:price:amount" content="24999">
    <meta property="product:price:currency" content="NPR"></head><body><h1>Redmi 14C 4/128</h1></body></html>"""
    p = GenericSource(SiteConfig(name="s", region="np")).parse(Selector(html, url="https://s/p"))
    assert p.offers[0].price == 24999 and p.offers[0].currency == "NPR"


def test_visible_rupee_price_on_store_pages():
    html = "<html><body><h1>Oppo A5 Pro 8/256</h1><p>Price: Rs. 35,999</p></body></html>"
    p = GenericSource(SiteConfig(name="s", region="np")).parse(Selector(html, url="https://s/p"))
    assert p.offers[0].price == 35999


def test_user_source_list_gets_new_defaults_but_keeps_edits(tmp_path, monkeypatch):
    from devicescout import paths
    monkeypatch.setenv("DEVICESCOUT_HOME", str(tmp_path))
    old = {"sources": [
        {"name": "daraz-np", "type": "daraz", "enabled": True},
        {"name": "hukut", "type": "auto", "base_url": "https://hukut.com", "enabled": False},
        {"name": "my-shop", "type": "auto", "base_url": "https://my.shop.com.np"},
    ], "removed_by_user": ["pricenepal"]}
    (tmp_path / "sources.json").write_text(json.dumps(old))
    doc = json.loads(paths.sources_path().read_text())
    names = [e["name"] for e in doc["sources"]]
    assert "daraz-np" not in names                          # retired default
    assert "my-shop" in names and "gadgetbyte" in names      # user's own kept, new default added
    assert "pricenepal" not in names                         # user removed it: stays removed
    hukut = next(e for e in doc["sources"] if e["name"] == "hukut")
    assert hukut["enabled"] is False                         # user's choice kept
    assert hukut["start_urls"] == ["https://hukut.com/mobile-phones", "https://hukut.com/laptops"]
    assert paths.merge_default_sources(tmp_path / "sources.json") is False     # only once


def test_div_spec_sheet_is_read_and_variant_prices_are_not_specs():
    html = """<html><body><h1>OnePlus 15R</h1>
    <script type="application/ld+json">{"@type":"Product","name":"OnePlus 15R",
      "offers":{"@type":"Offer","price":"98499","priceCurrency":"NPR"}}</script>
    <table><tr><th>12/256GB</th><td>Rs. 98,499</td></tr></table>
    <div class="product-specs"><h3>Display</h3>
      <div class="row"><span>Screen Size</span><span>6.83 inches</span></div>
      <div class="row"><span>Refresh Rate</span><span>165Hz</span></div>
      <h3>Battery</h3>
      <div class="row"><p>Battery</p><p>7400 mAh</p></div>
      <div class="row"><p>Charging:</p><p>80W SUPERVOOC</p></div>
    </div></body></html>"""
    p = GenericSource(SiteConfig(name="s", region="np")).parse(Selector(html, url="https://s/oneplus-15r"))
    assert p.raw_specs["Display / Screen Size"] == "6.83 inches"
    assert p.raw_specs["Battery / Charging"] == "80W SUPERVOOC"
    assert "12/256GB" not in p.raw_specs
    assert p.specs["battery_mah"] == 7400


def test_price_in_plain_html_but_specs_only_in_browser_renders_the_page():
    shell = ('<html><body><h1>X Phone</h1><script type="application/ld+json">{"@type":"Product",'
             '"name":"X Phone","offers":{"price":"50000","priceCurrency":"NPR"}}</script>'
             '<script>' + "x" * 60000 + '</script></body></html>')
    full = shell.replace("</h1>", '</h1><div class="specs">' + "".join(
        f'<div><span>Spec {i}</span><span>value {i}</span></div>' for i in range(8)) + "</div>")

    class F:
        modes: list = []

        def get(self, url, mode="static", **kw):
            self.modes.append(mode)
            return Selector(full if mode == "dynamic" else shell, url=url)

    src, f = GenericSource(SiteConfig(name="s", region="np")), F()
    p = src._product(f, "https://s/x-phone")
    assert len(p.raw_specs) == 8 and f.modes == ["static", "dynamic"]
    src._product(f, "https://s/y-phone")
    assert src.fetch_mode == "dynamic"      # two wins: the rest go straight to the browser


def test_div_spec_rows_keep_sections_apart_and_skip_pairs_of_rows():
    # hukut's layout: labels such as "Type" repeat per section; a wrapper holding two rows
    # side by side must not become "Type Li-Ion 7400 mAh" = "Charging 80W wired".
    row = '<div class="grid"><div class="text-sm">{}</div><div class="col-span-2">{}</div></div>'
    html = ('<html><body><h1>P</h1><script type="application/ld+json">{"@type":"Product","name":"P",'
            '"offers":{"price":"1000","priceCurrency":"NPR"}}</script><div id="specification">'
            '<h2>Product Specification</h2>'
            '<div><h3>DISPLAY</h3><div>' + row.format("Type", "AMOLED, 120Hz") + '</div></div>'
            '<div><h3>BATTERY</h3><div>' + row.format("Type", "Li-Ion 7400 mAh")
            + row.format("Charging", "80W wired") + '</div></div></div></body></html>')
    p = GenericSource(SiteConfig(name="s", region="np")).parse(Selector(html, url="https://s/p"))
    assert p.raw_specs == {"Display / Type": "AMOLED, 120Hz", "Battery / Type": "Li-Ion 7400 mAh",
                           "Battery / Charging": "80W wired"}
    assert p.specs["battery_mah"] == 7400 and p.specs["charging_w"] == 80
