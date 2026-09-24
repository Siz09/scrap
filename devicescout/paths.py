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
    """The user's editable copy of the source registry, created from the shipped default and
    brought up to date when a newer default ships (see merge_default_sources)."""
    user = data_dir() / "sources.json"
    if not user.exists() or user.stat().st_size == 0:   # empty = a failed copy or an empty bind mount
        shutil.copyfile(packaged("data/sources.json"), user)
    else:
        try:
            merge_default_sources(user)
        except (OSError, ValueError) as e:  # a broken user file is reported on the Data sources page
            import logging
            logging.getLogger(__name__).warning("couldn't update %s from the shipped defaults: %s", user, e)
    return user


def merge_default_sources(user: Path) -> bool:
    """Apply a newer shipped source list to the user's copy without losing their changes:
    new default sources are added (unless the user removed them), settings missing from a
    user's entry are filled in, and sources the defaults retired are dropped.
    Returns True if the file changed."""
    import json
    shipped = json.loads(packaged("data/sources.json").read_text(encoding="utf-8"))
    mine = json.loads(user.read_text(encoding="utf-8"))
    if mine.get("defaults_version", 1) >= shipped.get("defaults_version", 1):
        return False
    removed = set(mine.get("removed_by_user", []))
    entries = [e for e in mine["sources"] if e["name"] not in set(shipped.get("retired", []))]
    by_name = {e["name"]: e for e in entries}
    for default in shipped["sources"]:
        if default["name"] in removed:
            continue
        if default["name"] in by_name:
            for k, v in default.items():
                by_name[default["name"]].setdefault(k, v)
            if "notes" in default:
                by_name[default["name"]]["notes"] = default["notes"]
        else:
            entries.append(default)
    mine["sources"] = entries
    mine["defaults_version"] = shipped["defaults_version"]
    tmp = user.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(mine, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(user)
    return True


def web_dist() -> Path | None:
    d = packaged("web/dist")
    return d if (d / "index.html").exists() else None
