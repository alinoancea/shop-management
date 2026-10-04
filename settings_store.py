"""Persistent settings storage on disk."""
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_SETTINGS_PATH = Path("settings.json")
_DEFAULT = {"dbf_files": [], "screen_refresh_seconds": 60, "store_name": ""}


def _ensure_settings_path():
    """Resolve path relative to app location."""
    return Path(__file__).parent / _SETTINGS_PATH.name


def load_settings() -> dict:
    """Load settings from disk. Returns default if file missing."""
    path = _ensure_settings_path()
    if not path.exists():
        return _DEFAULT.copy()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if "dbf_files" not in data:
            data["dbf_files"] = []
        data.setdefault("screen_refresh_seconds", _DEFAULT["screen_refresh_seconds"])
        data.setdefault("store_name", _DEFAULT["store_name"])
        return data
    except Exception as e:
        logger.exception("Failed to load settings: %s", e)
        return _DEFAULT.copy()


def save_settings(data: dict) -> None:
    """Save settings to disk."""
    path = _ensure_settings_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    logger.info("Settings saved to %s", path)


def get_dbf_files() -> list[str]:
    """Get list of configured DBF file paths."""
    return load_settings().get("dbf_files", [])


def set_dbf_files(paths: list[str]) -> None:
    """Set DBF file paths and persist to disk."""
    # Deduplicate and filter empty
    paths = list(dict.fromkeys(p.strip() for p in paths if p and p.strip()))
    data = load_settings()
    data["dbf_files"] = paths
    save_settings(data)


def add_dbf_file(path: str) -> None:
    """Add a DBF file path if not already present."""
    path = path.strip()
    if not path:
        return
    data = load_settings()
    if path not in data["dbf_files"]:
        data["dbf_files"].append(path)
        save_settings(data)


def get_store_name() -> str:
    """Store name shown under the app title (empty if not set)."""
    return load_settings()["store_name"]


def set_store_name(name: str) -> None:
    data = load_settings()
    data["store_name"] = name
    save_settings(data)


def get_screen_refresh() -> int:
    """Seconds between configuration requests from screen devices."""
    return load_settings()["screen_refresh_seconds"]


def set_screen_refresh(seconds: int) -> None:
    data = load_settings()
    data["screen_refresh_seconds"] = seconds
    save_settings(data)


def remove_dbf_file(path: str) -> None:
    """Remove a DBF file path."""
    data = load_settings()
    data["dbf_files"] = [p for p in data["dbf_files"] if p != path]
    save_settings(data)
