"""Every device shows an image: its photo, one found on its page, or a drawn placeholder."""

from fastapi.testclient import TestClient

from devicescout import images
from devicescout.models import Category, Offer, Product
from devicescout.paths import packaged
from devicescout.server import create_app
from devicescout.storage import Store

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 400


def app_with(tmp_path, *products):
    db = tmp_path / "x.db"
    s = Store(db)
    keys = [s.upsert(p) for p in products]
    s.close()
    return TestClient(create_app(db, packaged("data/sources.json"), read_only=True)), keys, db


def phone(name, image=None, url="https://shop.com.np/p/x"):
    return Product(source="shop", url=url, name=name, brand=name.split()[0], category=Category.PHONE,
                   image=image, offers=[Offer("shop", url, 45000, "NPR")])


def test_photo_is_downloaded_once_then_served_from_disk(tmp_path, monkeypatch):
    calls = []

    def fake_get(url, referer=None, limit=images.MAX_BYTES):
        calls.append((url, referer))
        return PNG, "image/png"
    monkeypatch.setattr(images, "_get", fake_get)
    client, (key,), _ = app_with(tmp_path, phone("Koshi K5", image="https://cdn.shop.com.np/k5.png"))
    for _ in range(2):
        r = client.get(f"/api/images/{key}")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content == PNG
    assert calls == [("https://cdn.shop.com.np/k5.png", "https://shop.com.np/p/x")]   # the store's page as referer


def test_image_found_on_the_product_page_is_remembered(tmp_path, monkeypatch):
    page = ('<html><head><meta property="og:image" content="/media/k5-front.webp"></head>'
            '<body>Koshi K5</body></html>').encode()

    def fake_get(url, referer=None, limit=images.MAX_BYTES):
        if url == "https://shop.com.np/p/x":
            return page, "text/html"
        if url == "https://shop.com.np/media/k5-front.webp":
            return PNG, "image/webp"
        raise OSError(url)
    monkeypatch.setattr(images, "_get", fake_get)
    client, (key,), db = app_with(tmp_path, phone("Koshi K5"))
    r = client.get(f"/api/images/{key}")
    assert r.headers["content-type"] == "image/webp"
    assert Store(db).product(key).image == "https://shop.com.np/media/k5-front.webp"


def test_placeholder_when_no_site_has_an_image(tmp_path, monkeypatch):
    def offline(url, referer=None, limit=images.MAX_BYTES):
        raise OSError("no image anywhere")
    monkeypatch.setattr(images, "_get", offline)
    client, (key,), _ = app_with(tmp_path, phone("Koshi K5 <Lite>"))
    r = client.get(f"/api/images/{key}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert "Koshi K5 &lt;Lite&gt;" in r.text and "No photo yet" in r.text
    assert client.get("/api/images/no-such-device").status_code == 404


def test_image_from_page_variants():
    assert images.image_from_page('<meta content="https://x/a.jpg" property="og:image">', "https://x/p") == "https://x/a.jpg"
    assert images.image_from_page('<meta name="twitter:image" content="b.png">', "https://x/p/") == "https://x/p/b.png"
    ld = '<script type="application/ld+json">{"@type": "Product", "image": ["https://x/c.webp"]}</script>'
    assert images.image_from_page(ld, "https://x/p") == "https://x/c.webp"
    assert images.image_from_page("<html>no image</html>", "https://x/p") is None
