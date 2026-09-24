import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Keep tests out of the real per-user data directory."""
    monkeypatch.setenv("DEVICESCOUT_HOME", str(tmp_path / "home"))
