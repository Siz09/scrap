"""Web API, against the fictional sample catalogue."""

import time

import pytest
from fastapi.testclient import TestClient

from devicescout.paths import packaged
from devicescout.sample import build_sample
from devicescout.server import create_app


@pytest.fixture
def client(tmp_path):
    db = build_sample(tmp_path / "sample.db")
    return TestClient(create_app(db, packaged("data/sources.json"), sample=True))


def test_meta(client):
    m = client.get("/api/meta").json()
    phone = next(c for c in m["categories"] if c["id"] == "phone")
    assert {"photography", "gaming", "battery"} <= {u["id"] for u in phone["uses"]}
    assert any(x["key"] == "has_5g" for x in phone["musts"])
    assert m["stats"]["products"] == 22 and m["sample"] is True
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
    assert any(x["name"] == "daraz-np" for x in s)
    assert client.post("/api/jobs", json={"kind": "check"}).status_code == 409


def test_read_only_blocks_jobs(tmp_path):
    c = TestClient(create_app(tmp_path / "x.db", packaged("data/sources.json"), read_only=True))
    assert c.post("/api/jobs", json={"kind": "scrape"}).status_code == 403


def test_job_runs_in_background(tmp_path, monkeypatch):
    import devicescout.server as server

    def fake_check(entries, log, cancel, **_):
        for e in entries:
            log(f"{e['name']} OK")
        return [{"name": e["name"], "status": "OK"} for e in entries]

    monkeypatch.setattr(server, "run_check", fake_check)
    c = TestClient(create_app(tmp_path / "x.db", packaged("data/sources.json")))
    job = c.post("/api/jobs", json={"kind": "check", "names": ["daraz-np", "hukut"]}).json()
    for _ in range(50):
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] != "running":
            break
        time.sleep(0.02)
    assert j["status"] == "done" and len(j["result"]) == 2 and "hukut OK" in j["log"][-1]


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
