import os

import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Keep tests out of the real per-user data directory."""
    monkeypatch.setenv("DEVICESCOUT_HOME", str(tmp_path / "home"))


PG_URL = os.getenv("DEVICESCOUT_TEST_PG")   # e.g. postgresql://postgres@localhost/devicescout_test


@pytest.fixture
def pg_url():
    """A fresh PostgreSQL database for one test (skipped when DEVICESCOUT_TEST_PG isn't set)."""
    if not PG_URL:
        pytest.skip("set DEVICESCOUT_TEST_PG to run the PostgreSQL tests")
    import psycopg

    from devicescout.pgstore import PgStore
    with psycopg.connect(PG_URL, autocommit=True) as c:
        for schema in ("raw", "clean", "category", "ops"):
            c.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    PgStore._ready.discard(PG_URL)
    return PG_URL
