"""
actions/routines.py — scheduled recurring commands ("every day at 9, open VS Code").

WHAT A ROUTINE IS
    A stored natural-language command plus a time-of-day and a day filter.
    When it comes due, main.py injects it into the Live session exactly like a
    proactive check-in — FRIDAY announces it and executes it with its normal
    tools, so every safety layer (confirm gate, plan approval for multi-step
    goals) still applies. A routine is standing *permission to start*, never
    permission to skip a confirmation.

WHY A GRACE WINDOW
    Due-ness is "scheduled time was within the last GRACE_MINUTES and it has
    not run today". Without the window, launching FRIDAY at 14:00 would fire
    every routine scheduled since midnight in one avalanche; with it, a missed
    routine is simply skipped until tomorrow — assistants that pile up stale
    jobs stop being trusted with jobs.

STORAGE
    data/routines.json, guarded by a module lock. Encrypted at rest by
    core.secure_store (AES-256-GCM). Same read-modify-write pattern as the
    other small JSON stores in this project.
"""

from __future__ import annotations

import re
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path

from core.secure_store import read_json, write_json

GRACE_MINUTES = 10
MAX_ROUTINES  = 20

_DAY_KEYS  = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_DAY_SETS  = {
    "daily":    set(_DAY_KEYS),
    "weekdays": {"mon", "tue", "wed", "thu", "fri"},
    "weekends": {"sat", "sun"},
}

_lock = threading.Lock()


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


ROUTINES_PATH = _base_dir() / "data" / "routines.json"


def _load() -> list[dict]:
    try:
        data = read_json(ROUTINES_PATH)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(routines: list[dict]) -> None:
    ROUTINES_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_json(ROUTINES_PATH, routines)


def _parse_time(raw: str) -> str | None:
    """Accept '9', '9:30', '09:30', '9.30', '9 30' → 'HH:MM'; None if invalid."""
    m = re.match(r"^\s*(\d{1,2})(?:[:. ](\d{1,2}))?\s*$", str(raw or ""))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return f"{h:02d}:{mi:02d}"


def _parse_days(raw) -> list[str] | None:
    """'daily' | 'weekdays' | 'weekends' | 'mon,wed,fri' → canonical day list."""
    if not raw:
        return list(_DAY_KEYS)
    if isinstance(raw, str):
        key = raw.lower().strip()
        if key in _DAY_SETS:
            return [d for d in _DAY_KEYS if d in _DAY_SETS[key]]
        parts = [p.strip().lower()[:3] for p in key.split(",") if p.strip()]
    else:
        parts = [str(p).strip().lower()[:3] for p in raw]
    days = [d for d in _DAY_KEYS if d in parts]
    return days or None


def _describe(r: dict) -> str:
    days = r.get("days", list(_DAY_KEYS))
    if set(days) == _DAY_SETS["daily"]:
        when = "daily"
    elif set(days) == _DAY_SETS["weekdays"]:
        when = "weekdays"
    elif set(days) == _DAY_SETS["weekends"]:
        when = "weekends"
    else:
        when = ",".join(days)
    return f"[{r['id'][:6]}] {r['time']} {when} — {r['command']}"


# ── Public API ─────────────────────────────────────────────────────────────────

def add_routine(time_str: str, command: str, days=None) -> str:
    t = _parse_time(time_str)
    if not t:
        return f"'{time_str}' is not a valid time. Use HH:MM, e.g. 09:30."
    command = (command or "").strip()
    if not command:
        return "A routine needs a command to run."
    day_list = _parse_days(days)
    if not day_list:
        return f"'{days}' is not a valid day filter. Use daily, weekdays, weekends, or day names."

    with _lock:
        routines = _load()
        if len(routines) >= MAX_ROUTINES:
            return f"Routine limit reached ({MAX_ROUTINES}). Remove one first."
        r = {
            "id":       uuid.uuid4().hex,
            "time":     t,
            "days":     day_list,
            "command":  command[:300],
            "last_run": "",
        }
        routines.append(r)
        _save(routines)
    return f"Routine saved: {_describe(r)}"


def remove_routine(ident: str) -> str:
    ident = (ident or "").strip().lower()
    if not ident:
        return "Say which routine to remove — its id, time, or a word from its command."
    with _lock:
        routines = _load()
        keep, removed = [], []
        for r in routines:
            hit = (
                r["id"].startswith(ident)
                or r["time"] == _parse_time(ident)
                or ident in r["command"].lower()
            )
            (removed if hit else keep).append(r)
        if not removed:
            return f"No routine matches '{ident}'."
        if len(removed) > 1:
            return ("That matches several routines: "
                    + "; ".join(_describe(r) for r in removed)
                    + ". Use the id to pick one.")
        _save(keep)
    return f"Removed: {_describe(removed[0])}"


def list_routines() -> str:
    routines = _load()
    if not routines:
        return "No routines are set."
    return "Routines:\n" + "\n".join(_describe(r) for r in routines)


def due_routines(now: datetime | None = None) -> list[dict]:
    """Return routines due right now and stamp them as run today.

    Called every ~30 s by the background loop, so 'due' must be idempotent
    per day: scheduled time within the last GRACE_MINUTES, today matches the
    day filter, not already run today."""
    now = now or datetime.now()
    today     = now.strftime("%Y-%m-%d")
    day_key   = _DAY_KEYS[now.weekday()]
    minutes   = now.hour * 60 + now.minute

    due = []
    with _lock:
        routines = _load()
        changed = False
        for r in routines:
            if r.get("last_run") == today or day_key not in r.get("days", []):
                continue
            try:
                h, mi = map(int, r["time"].split(":"))
            except Exception:
                continue
            sched = h * 60 + mi
            if 0 <= minutes - sched <= GRACE_MINUTES:
                r["last_run"] = today
                due.append(dict(r))
                changed = True
        if changed:
            _save(routines)
    return due
