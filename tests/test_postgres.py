"""PostgreSQL store: raw layer per website, clean catalogue, category views, search, queue.

Runs when DEVICESCOUT_TEST_PG points at a database (CI provides one); skipped otherwise."""

import psycopg
from psycopg.rows import dict_row

from devicescout.models import Category, Offer, Product
from devicescout.pgstore import PgStore, import_sqlite
from devicescout.pipeline import IngestStats, ingest, reprocess
from devicescout.storage import Store


def phone(source="hukut", name="Samsung Galaxy S25 Ultra 12/256", price=184999, **specs):
    return Product(source=source, url=f"https://{source}.com/{name.lower().replace(' ', '-')}", name=name,
                   brand="Samsung", category=Category.PHONE,
                   specs={"ram_gb": 12, "battery_mah": 5000, "has_5g": True, "chipset": "Snapdragon 8 Elite",
                          **specs},
                   offers=[Offer(source, f"https://{source}.com/x", price, "NPR", seller=source)])


def q(url, sql, *args):
    with psycopg.connect(url, row_factory=dict_row) as c:
        return c.execute(sql, args).fetchall()


def test_raw_layer_is_one_partition_per_website(pg_url):
    s = PgStore(pg_url)
    ingest(s, phone("hukut"))
    ingest(s, phone("oliz-store", price=182000))
    ingest(s, phone("hukut"))                          # same content again: not stored twice
    parts = {r["relname"] for r in q(pg_url, "SELECT c.relname FROM pg_inherits i JOIN pg_class c "
                                             "ON c.oid = i.inhrelid WHERE i.inhparent = 'raw.records'::regclass")}
    assert parts == {"records_hukut", "records_oliz_store"}
    assert q(pg_url, "SELECT count(*) AS n FROM raw.records_hukut")[0]["n"] == 1
    assert {r["source"]: r["records"] for r in s.raw_summary()} == {"hukut": 1, "oliz-store": 1}


def test_every_fetched_page_is_kept_once_per_version(pg_url):
    s = PgStore(pg_url)
    s.record_page("hukut", "https://hukut.com/mobile-phones", 200, "scrapling-dynamic", "text/html", b"<html>v1</html>")
    s.record_page("hukut", "https://hukut.com/mobile-phones", 200, "scrapling-http", "text/html", b"<html>v1</html>")
    s.record_page("hukut", "https://hukut.com/mobile-phones", 200, "scrapling-http", "text/html", "<html>v2\x00</html>")
    rows = q(pg_url, "SELECT body, times_seen FROM raw.pages_hukut ORDER BY id")
    assert [(r["body"], r["times_seen"]) for r in rows] == [("<html>v1</html>", 2), ("<html>v2</html>", 1)]


def test_category_views_have_typed_columns_and_nepali_prices(pg_url):
    s = PgStore(pg_url)
    ingest(s, phone("hukut"))
    ingest(s, phone("oliz-store", price=182000))
    row = q(pg_url, "SELECT * FROM category.phone")[0]
    assert row["ram_gb"] == 12.0 and row["has_5g"] is True and row["chipset"] == "Snapdragon 8 Elite"
    assert (row["lowest_price_npr"], row["highest_price_npr"], row["sellers"]) == (182000, 184999, 2)
    assert q(pg_url, "SELECT count(*) AS n FROM category.laptop")[0]["n"] == 0
    ingest(s, phone("koshi-mart", price=183000))
    ingest(s, phone("bait-deals", price=59999))           # a third of the market price: bait
    row = q(pg_url, "SELECT * FROM category.phone")[0]
    assert (row["lowest_price_npr"], row["sellers"]) == (182000, 3)


def test_search_prefix_then_typo_tolerant(pg_url):
    s = PgStore(pg_url)
    key = s.upsert(phone())
    assert s.search("s25 ultr") == [key]
    assert s.search("snapdragon elite", Category.PHONE) == [key]
    assert s.search("galxy s25") == [key]              # typo: trigram fallback
    assert s.search("iphone") == []
    assert s.search("s25", Category.LAPTOP) == []


def test_reprocess_rebuilds_catalogue_from_raw(pg_url):
    s = PgStore(pg_url)
    stats = IngestStats()
    ingest(s, phone("hukut"), stats)
    ingest(s, phone("oliz-store", price=182000), stats)
    s.log_issue("hukut", "u", "fixed", "name", "x")
    stats = reprocess(s)
    assert stats.stored == 2 and s.stats()["products"] == 1 and s.stats()["raw_records"] == 2


def test_price_history_and_latest_offers(pg_url):
    s = PgStore(pg_url)
    p = phone()
    p.offers[0].scraped_at = "2026-08-01T00:00:00+00:00"
    s.upsert(p)
    p2 = phone(price=179999)
    p2.offers[0].scraped_at = "2026-09-01T00:00:00+00:00"
    key = s.upsert(p2)
    assert [r["price"] for r in s.price_history(key)] == [184999, 179999]
    assert s.price_history(key)[0]["scraped_at"].startswith("2026-08-01T00:00:00")
    assert [o.price for o in s.product(key).offers] == [179999]


def test_queue_claims_each_job_once(pg_url):
    a, b = PgStore(pg_url), PgStore(pg_url)
    job = a.enqueue_job("scrape", ["hukut"], None, origin="ui")
    assert b.claim_job()["id"] == job["id"]
    assert a.claim_job() is None
    a.job_log(job["id"], "hello")
    a.finish_job(job["id"], "done")
    assert b.job(job["id"])["log"] == ["hello"] and b.job(job["id"])["status"] == "done"
    a.set_kv("x", "1")
    a.set_kv("x", "2")
    assert b.get_kv("x") == "2"


def test_import_from_sqlite(pg_url, tmp_path):
    old = Store(tmp_path / "old.db")
    ingest(old, phone("hukut"))
    old.close()
    s = PgStore(pg_url)
    assert import_sqlite(tmp_path / "old.db", s, log=lambda _: None) == 1
    assert s.stats()["products"] == 1


def test_queue_lanes(pg_url):
    s = PgStore(pg_url)
    sched = s.enqueue_job("scrape", [], None, origin="schedule")
    mine = s.enqueue_job("check", ["hukut"], None, origin="ui")
    assert s.claim_job(exclude_origin="schedule")["id"] == mine["id"]
    assert s.claim_job(exclude_origin="schedule") is None
    assert s.claim_job(job_id=sched["id"])["id"] == sched["id"]
    s.request_cancel(mine["id"])
    assert s.job(mine["id"])["cancel_requested"] and not s.job(sched["id"])["cancel_requested"]
