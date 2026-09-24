"""Where the installed app keeps its files.

The database, the editable source list and caches live in the per-user data
directory, never inside the installed package. In Docker, DEVICESCOUT_HOME=/data
(a volume shared by the website and scraper containers).
"""

from __future__ import annotations

import os
import shutil
import sys
from importlib import resources
from pathlib import Path


def data_dir() -> Path:
    if env := os.getenv("DEVICESCOUT_HOME"):
        d = Path(env)
    elif sys.platform == "win32":
        d = Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "DeviceScout"
    elif sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / "DeviceScout"
    else:
        d = Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "devicescout"
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_db() -> Path:
    return data_dir() / "devicescout.db"


def sample_db() -> Path:
    return data_dir() / "sample.db"


def detect_cache() -> Path:
    return data_dir() / "detected_platforms.json"


def source_status() -> Path:
    return data_dir() / "source_status.json"


def packaged(name: str) -> Path:
    """A file shipped inside the package (works from a source checkout or an installed wheel)."""
    return Path(str(resources.files("devicescout") / name))


def sources_path() -> Path:
    """The user's editable copy of the source registry, created from the shipped default."""
    user = data_dir() / "sources.json"
    if not user.exists() or user.stat().st_size == 0:   # empty = a failed copy or an empty bind mount
        shutil.copyfile(packaged("data/sources.json"), user)
    return user


def web_dist() -> Path | None:
    d = packaged("web/dist")
    return d if (d / "index.html").exists() else None
