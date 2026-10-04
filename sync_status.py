"""Sync status for UI display."""
import threading
from datetime import datetime

_lock = threading.Lock()
_status = {
    "in_progress": False,
    "last_result": None,
    "last_finished_at": None,
}


def set_in_progress(value: bool) -> None:
    with _lock:
        _status["in_progress"] = value


def set_result(result: dict) -> None:
    with _lock:
        _status["last_result"] = result
        _status["last_finished_at"] = result.get("finished_at")
        _status["in_progress"] = False


def get_status() -> dict:
    with _lock:
        return {
            "in_progress": _status["in_progress"],
            "last_result": _status["last_result"],
            "last_finished_at": _status["last_finished_at"],
        }
