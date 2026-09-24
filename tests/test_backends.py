"""The multi-scraper fallback chain, with scripted fake backends (no network)."""

import pytest

from devicescout.sources.backends import Backend, FetchedPage, NotFound, block_reason
from devicescout.sources.base import Fetcher

PRODUCT_HTML = b"<html><body><h1>Phone</h1>" + b"x" * 2000 + b"</body></html>"


class Scripted(Backend):
    def __init__(self, name, responses, browser=False):
        self.name, self.responses, self.browser, self.calls = name, list(responses), browser, 0

    def fetch(self, url, headers):
        self.calls += 1
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(r, Exception):
            raise r
        status, body = r
        return FetchedPage(body, url, status, self.name)


def fetcher(*backends):
    return Fetcher(delay=0, respect_robots=False, backends=list(backends))


def test_falls_through_blocked_backend_and_remembers_winner():
    a = Scripted("a", [(403, b"denied")])
    b = Scripted("b", [(200, PRODUCT_HTML)])
    f = fetcher(a, b)
    assert f.get("https://shop.com.np/p/1").css("h1::text").get() == "Phone"
    f.get("https://shop.com.np/p/2")
    assert (a.calls, b.calls) == (1, 2)            # second request went straight to b
    assert f.stats["a"]["blocked"] == 1 and f.stats["b"]["ok"] == 2
    assert "b 2 ok" in f.summary()


def test_bot_wall_with_200_status_is_a_block():
    wall = b"<html><title>Attention Required! | Cloudflare</title>" + b" " * 500 + b"</html>"
    f = fetcher(Scripted("a", [(200, wall)]), Scripted("b", [(200, PRODUCT_HTML)]))
    assert f.get("https://x.com.np/").backend == "b"


def test_404_is_an_answer_not_a_block():
    b = Scripted("b", [(200, PRODUCT_HTML)])
    with pytest.raises(NotFound):
        fetcher(Scripted("a", [(404, b"nope")]), b).get("https://x.com.np/gone")
    assert b.calls == 0


def test_json_requests_reject_html_and_read_browser_rendered_json():
    html = Scripted("http1", [(200, PRODUCT_HTML)])                       # bot wall / HTML instead of JSON
    browser = Scripted("browser", [(200, b'<html><body><pre>{"products": [1]}</pre></body></html>')],
                       browser=True)
    assert fetcher(html, browser).get_json("https://x.com.np/products.json") == {"products": [1]}
    assert html.calls == 1 and browser.calls == 1


def test_probes_skip_browsers_but_try_http_scrapers():
    a = Scripted("a", [(403, b"denied")])
    b = Scripted("b", [(200, b'{"ok": 1}')])
    browser = Scripted("browser", [(200, b'<pre>{"ok": 2}</pre>')], browser=True)
    assert fetcher(a, browser, b).get_json("https://x.com.np/products.json", fallback="http") == {"ok": 1}
    assert browser.calls == 0


def test_first_scraper_only():
    a = Scripted("a", [(403, b"denied")])
    b = Scripted("b", [(200, b'{"ok": 1}')])
    with pytest.raises(RuntimeError):
        fetcher(a, b).get_json("https://x.com.np/wp-json/x", fallback=False)
    assert b.calls == 0


def test_small_sitemaps_are_not_blocks():
    xml = b"<?xml version='1.0'?><urlset><url><loc>https://a/p/1</loc></url></urlset>"
    assert block_reason(FetchedPage(xml, "u", 200, "t")) is None


def test_crashing_browser_is_disabled_for_the_run():
    browser = Scripted("browser", [RuntimeError("Executable doesn't exist")], browser=True)
    ok = Scripted("http", [(200, PRODUCT_HTML)])
    f = Fetcher(delay=0, respect_robots=False, backends=[browser, ok], mode="dynamic")
    f.get("https://a.com.np/1")
    f.get("https://b.com.np/1")
    assert browser.calls == 1


def test_all_blocked_reports_every_reason():
    f = fetcher(Scripted("a", [(429, b"slow down")]), Scripted("b", [(503, b"x")]))
    with pytest.raises(RuntimeError, match=r"a: HTTP 429; b: HTTP 503"):
        f.get("https://x.com.np/")


def test_block_reason():
    assert block_reason(FetchedPage(PRODUCT_HTML, "u", 200, "t")) is None
    assert block_reason(FetchedPage(b"<html></html>", "u", 200, "t")) == "near-empty page"
    assert block_reason(FetchedPage(b"[1,2]", "u", 200, "t"), want_json=True) is None
