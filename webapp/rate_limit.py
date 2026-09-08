"""A tiny file-backed daily counter so a bug can't run up the API bill unattended.

Not related to runs/predictions.db -- that database is the batch eval
pipeline's history of scored experiments. This counts real-money calls made
by the interactive webapp, independent of the batch runs, and survives a
server restart (an in-memory counter wouldn't).
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

COUNTER_PATH = Path(__file__).parent.parent / "runs" / "webapp_daily_counter.json"
DAILY_LIMIT = 50

_lock = threading.Lock()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class DailyLimitExceeded(Exception):
    pass


def check_and_increment(limit: int = DAILY_LIMIT) -> int:
    """Raise DailyLimitExceeded if today's count is already at the limit.

    Otherwise increments the count and returns the new value. Call this right
    before the paid model call, not before free/local work (e.g. reading a
    CSV row or fetching an image), so the count reflects real model calls.
    """
    with _lock:
        state = {"date": _today(), "count": 0}
        if COUNTER_PATH.exists():
            try:
                state = json.loads(COUNTER_PATH.read_text())
            except json.JSONDecodeError:
                pass
        if state.get("date") != _today():
            state = {"date": _today(), "count": 0}

        if state["count"] >= limit:
            raise DailyLimitExceeded(f"Daily prediction limit ({limit}) reached. Try again tomorrow.")

        state["count"] += 1
        COUNTER_PATH.parent.mkdir(parents=True, exist_ok=True)
        COUNTER_PATH.write_text(json.dumps(state))
        return state["count"]


def current_count() -> int:
    if not COUNTER_PATH.exists():
        return 0
    try:
        state = json.loads(COUNTER_PATH.read_text())
    except json.JSONDecodeError:
        return 0
    return state["count"] if state.get("date") == _today() else 0
