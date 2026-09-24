"""Web API, against the fictional sample catalogue."""

import time

import pytest
from fastapi.testclient import TestClient

from devicescout.paths import packaged
from devicescout.sample import build_sample
from devicescout.server import create_app


@pytest.fixture(params=["sqlite", "postgres"])
def client(request, tmp_path):
    db = build_sample(request.getfixturevalue("pg_url") if request.param == "postgres" else tmp_path / "sample.db")
    return TestClient(create_app(db, packaged("data/sources.json"), sample=True))


def test_meta(client):
    m = client.get("/api/meta").json()
    phone = next(c for c in m["categories"] if c["id"] == "phone")
    assert {"photography", "gaming", "battery"} <= {u["id"] for u in phone["uses"]}
    assert any(x["key"] == "has_5g" for x in phone["musts"])
    assert m["stats"]["products"] == 23 and m["sample"] is True
    assert m["specs"]["battery_mah"]["unit"] == "mAh"


def test_advise(client):
    r = client.post("/api/advise", json={"category": "phone", "budget_max": 60000,
                                         "uses": {"photography": 2, "battery": 1}, "os": ["android"],
                                         "must": [{"key": "has_5g", "op": "==", "value": True}]})
    assert r.status_code == 200
    body = r.json()
    names = [p["name"] for p in body["picks"]]
    assert names[0] == "Koshi K5 Camera"
    assert "Koshi K3 Lite" not in names                 # no 5G
    assert all(p["price_npr"] <= 60000 for p in body["picks"])
    top = body["picks"][0]
    assert top["key"] and top["where_to_buy"] and top["strengths"]
    assert top["price_npr"] > 20000                     # bait listing at 35% was ignored
    assert body["stretch_pick"] is None or body["stretch_pick"]["price_npr"] <= 69000


def test_advise_rejects_unknown_use(client):
    assert client.post("/api/advise", json={"category": "phone", "uses": {"astrology": 1}}).status_code == 400


def test_products_and_detail(client):
    r = client.get("/api/products", params={"category": "laptop", "sort": "price"}).json()
    assert r["total"] == 5
    prices = [i["best_price"] for i in r["items"]]
    assert prices == sorted(prices)
    assert client.get("/api/products", params={"q": "nimbus"}).json()["total"] == 3
    key = r["items"][0]["key"]
    d = client.get(f"/api/products/{key}").json()
    assert d["name"] == "Lumo Study 14" and d["offers"] and d["history"]
    assert client.get("/api/products/nope").status_code == 404


def test_bait_offer_marked_in_detail(client):
    items = client.get("/api/products", params={"q": "Nimbus Z9"}).json()["items"]
    d = client.get(f"/api/products/{items[0]['key']}").json()
    assert any(o["suspicious"] for o in d["offers"])
    assert d["best_price"] > 100000
    assert min(h["price"] for h in d["history"]) > 100000   # bait never charted as a price drop


def test_sources_listed_and_jobs_blocked_in_sample_mode(client):
    s = client.get("/api/sources").json()["sources"]
    assert any(x["name"] == "hukut" for x in s) and not any(x["name"] == "daraz-np" for x in s)
    assert client.post("/api/jobs", json={"kind": "check"}).status_code == 403


def test_read_only_blocks_jobs(tmp_path):
    c = TestClient(create_app(tmp_path / "x.db", packaged("data/sources.json"), read_only=True))
    assert c.post("/api/jobs", json={"kind": "scrape"}).status_code == 403


def test_job_runs_in_background(tmp_path, monkeypatch):
    import devicescout.jobs as jobs

    def fake_check(entries, log, cancel, progress, **_):
        for i, e in enumerate(entries):
            progress(done=i, total=len(entries), current=e["name"])
            log(f"{e['name']} OK")
        return [{"name": e["name"], "status": "OK"} for e in entries]

    monkeypatch.setattr(jobs, "run_check", fake_check)
    c = TestClient(create_app(tmp_path / "x.db", packaged("data/sources.json")))
    assert c.get("/api/meta").json()["jobs_mode"] == "local"
    job = c.post("/api/jobs", json={"kind": "check", "names": ["brother-mart", "hukut"]}).json()
    for _ in range(100):
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] not in ("queued", "running"):
            break
        time.sleep(0.05)
    assert j["status"] == "done" and any("hukut OK" in line for line in j["log"])
    assert j["progress"]["total"] == 2


def test_must_options_make_sense_per_category(client):
    cats = {c["id"]: c for c in client.get("/api/meta").json()["categories"]}
    laptop_weight = next(m for m in cats["laptop"]["musts"] if m["key"] == "weight_g")
    phone_weight = next(m for m in cats["phone"]["musts"] if m["key"] == "weight_g")
    assert min(laptop_weight["options"]) >= 1000 and max(phone_weight["options"]) < 300
    for c in cats.values():   # the UI keys controls by spec key: no duplicates within a category
        keys = [m["key"] for m in c["musts"]]
        assert len(keys) == len(set(keys))


def test_ask_endpoint(client):
    r = client.post("/api/ask", json={"q": "long lasting android phone under 1 lakh"}).json()
    assert r["parsed"]["uses"] == ["longevity"] and r["parsed"]["budget_max"] == 100000
    assert r["advice"]["picks"][0]["name"] == "Nimbus Mini S"      # 7 promised OS upgrades
    assert client.post("/api/parse", json={"q": "gaming laptop under 1.2 lakh"}).json()["category"] == "laptop"


def test_deals_endpoint(client):
    r = client.get("/api/deals").json()
    names = {d["name"]: d for d in r["items"]}
    assert {"Koshi K5 Camera", "Nimbus Watch 4", "Everest Book 14 Air"} <= set(names)
    assert "Lumo Power 7" not in names                               # paper discount hidden by default
    assert all(d["verified"] for d in r["items"])
    everything = {d["name"]: d for d in client.get("/api/deals", params={"verified_only": "false"}).json()["items"]}
    assert "paper discount" in everything["Lumo Power 7"]["verdicts"]
    assert "inflated original" in everything["Everest Charge 20K"]["verdicts"]
    assert names["Koshi K5 Camera"]["valid_until"]
    phones = client.get("/api/deals", params={"category": "phone"}).json()["items"]
    assert [d["name"] for d in phones] == ["Koshi K5 Camera"]


def test_admin_key_gates_the_queue(tmp_path):
    c = TestClient(create_app(tmp_path / "x.db", packaged("data/sources.json"), worker="external", admin_key="s3cret"))
    meta = c.get("/api/meta").json()
    assert meta["jobs_mode"] == "queue" and meta["admin_required"]
    assert c.post("/api/jobs", json={"kind": "check"}).status_code == 401
    assert c.post("/api/jobs", json={"kind": "check"}, headers={"X-Admin-Key": "wrong"}).status_code == 401
    assert c.post("/api/admin/verify", headers={"X-Admin-Key": "s3cret"}).json() == {"ok": True}
    job = c.post("/api/jobs", json={"kind": "check"}, headers={"X-Admin-Key": "s3cret"}).json()
    assert job["status"] == "queued"                                  # waits for the scraper container
    assert c.post("/api/jobs", json={"kind": "check"}, headers={"X-Admin-Key": "s3cret"}).status_code == 409
    assert c.post("/api/jobs/cancel", headers={"X-Admin-Key": "s3cret"}).json()["cancelled"] == 1
    assert c.get(f"/api/jobs/{job['id']}").json()["status"] == "cancelled"


def test_scraper_claims_queued_jobs_once(tmp_path):
    from devicescout.storage import Store
    s = Store(tmp_path / "x.db")
    a = s.enqueue_job("check", [], None, origin="ui")
    assert s.claim_job()["id"] == a["id"] and s.claim_job() is None


def test_broken_sources_file_is_reported_not_a_crash(tmp_path):
    bad = tmp_path / "sources.json"
    bad.write_text('{"sources": [ {"name": "x",, } ]}')
    c = TestClient(create_app(tmp_path / "x.db", bad))
    r = c.get("/api/sources")
    assert r.status_code == 200 and r.json()["sources"] == [] and "can't be read" in r.json()["error"]


def test_no_key_needed_by_default(tmp_path):
    c = TestClient(create_app(tmp_path / "x.db", packaged("data/sources.json"), worker="external", admin_key=""))
    assert c.get("/api/meta").json()["admin_required"] is False
    assert c.post("/api/jobs", json={"kind": "check"}).json()["status"] == "queued"


def test_manage_sources_from_the_page(tmp_path):
    import shutil
    src = tmp_path / "sources.json"
    shutil.copy(packaged("data/sources.json"), src)
    c = TestClient(create_app(tmp_path / "x.db", src, worker="external", admin_key=""))
    r = c.post("/api/sources", json={"url": "https://www.newstore.com.np/mobile-phones"}).json()
    assert r["source"]["name"] == "newstore" and r["source"]["base_url"] == "https://www.newstore.com.np"
    assert r["source"]["start_urls"] == ["https://www.newstore.com.np/mobile-phones"]
    assert r["job"]["names"] == ["newstore"]                               # checked straight away
    assert c.post("/api/sources", json={"url": "https://www.newstore.com.np"}).status_code == 409
    assert c.post("/api/sources", json={"url": "newstore"}).status_code == 400
    assert c.patch("/api/sources/newstore", json={"enabled": False}).json()["enabled"] is False
    assert c.delete("/api/sources/hukut").json()["removed"] == "hukut"
    names = {x["name"]: x for x in c.get("/api/sources").json()["sources"]}
    assert "hukut" not in names and names["newstore"]["enabled"] is False


def test_device_sold_only_abroad_is_shown_with_a_converted_price(client):
    m = client.get("/api/meta").json()
    assert m["rates"]["rates"]["USD"] > 0 and m["rates"]["source"]
    items = client.get("/api/products", params={"q": "himal fold"}).json()["items"]
    fold = next(p for p in items if p["name"] == "Himal Fold 2")
    assert fold["available_in_nepal"] is False and fold["best_price"] is None
    assert fold["converted_price"] == round(899 * m["rates"]["rates"]["USD"], 2)
    assert fold["converted_from"]["currency"] == "USD"
    picks = client.post("/api/advise", json={"category": "phone", "uses": {"balanced": 1}, "top": 20}).json()["picks"]
    assert any(p["name"] == "Himal Fold 2" and p["price_converted"] for p in picks)
    picks = client.post("/api/advise", json={"category": "phone", "uses": {"balanced": 1}, "top": 20,
                                             "nepal_only": True}).json()["picks"]
    assert not any(p["name"] == "Himal Fold 2" for p in picks)


def test_sorting_by_price_keeps_devices_sold_only_abroad(client):
    r = client.get("/api/products", params={"category": "phone", "sort": "price", "priced_only": "true"}).json()
    names = [p["name"] for p in r["items"]]
    assert "Himal Fold 2" in names and r["total"] == 9          # every phone, the import at its converted price
    prices = [p["best_price"] or p["converted_price"] for p in r["items"]]
    assert prices == sorted(prices)


def test_advice_can_list_every_match(client):
    few = client.post("/api/advise", json={"category": "phone", "uses": {"balanced": 1}}).json()
    every = client.post("/api/advise", json={"category": "phone", "uses": {"balanced": 1}, "top": 500}).json()
    assert len(few["picks"]) == 5 and len(every["picks"]) == every["considered"] > 5
    assert [p["name"] for p in every["picks"][:5]] == [p["name"] for p in few["picks"]]   # same order at the top
    assert all("best_listed_only" in p for p in every["picks"])
