"""
actions/followups.py — open loops that nag until the user closes them.

A reminder fires once and forgets. A follow-up is an open loop: "chase the
recruiter if they don't reply by Thursday" stays on the list and is spoken
again on a spaced schedule until the user says it's done, dropped, or snoozed.

STORAGE
    data/followups.json, encrypted at rest (AES-256-GCM), same lock +
    read-modify-write pattern as routines.

SPACING
    First nag on the due date. After that: 1 day, then 2, 4, capped at 7.
    At most one nag per loop per calendar day, so launching Friday late
    does not replay every overdue item in a pile-on.
"""

from __future__ import annotations

import re
import sys
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from core.secure_store import read_json, write_json

MAX_OPEN      = 40
MAX_NAG_DAYS  = 7
_DAY_NAMES    = ("monday", "tuesday", "wednesday", "thursday",
                 "friday", "saturday", "sunday")
_DAY_SHORT    = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

_lock = threading.Lock()


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


FOLLOWUPS_PATH = _base_dir() / "data" / "followups.json"


def _load() -> list[dict]:
    try:
        data = read_json(FOLLOWUPS_PATH)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(items: list[dict]) -> None:
    FOLLOWUPS_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_json(FOLLOWUPS_PATH, items)


def parse_due(raw: str, now: datetime | None = None) -> datetime | None:
    """Turn spoken dates into a datetime. Time defaults to 09:00."""
    now = now or datetime.now()
    text = (raw or "").strip().lower()
    if not text:
        return None

    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:[ t](\d{1,2}):(\d{2}))?$", text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        h, mi = int(m.group(4) or 9), int(m.group(5) or 0)
        try:
            return datetime(y, mo, d, h, mi)
        except ValueError:
            return None

    if text in ("today",):
        return now.replace(hour=9, minute=0, second=0, microsecond=0)

    if text in ("tomorrow",):
        t = now + timedelta(days=1)
        return t.replace(hour=9, minute=0, second=0, microsecond=0)

    m = re.match(r"^in\s+(\d+)\s+(hour|hours|day|days|week|weeks)\s*$", text)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if unit.startswith("hour"):
            return now + timedelta(hours=n)
        if unit.startswith("week"):
            return now + timedelta(weeks=n)
        return now + timedelta(days=n)

    next_week = text.startswith("next ")
    day_part = text[5:].strip() if next_week else text
    if day_part in _DAY_NAMES:
        target = _DAY_NAMES.index(day_part)
    elif day_part in _DAY_SHORT:
        target = _DAY_SHORT.index(day_part)
    else:
        return None

    delta = (target - now.weekday()) % 7
    if delta == 0:
        delta = 7 if next_week or now.hour >= 18 else 0
    elif next_week:
        delta += 7
    t = now + timedelta(days=delta)
    return t.replace(hour=9, minute=0, second=0, microsecond=0)


def _interval_days(nags: int) -> int:
    """1, 2, 4, 7, 7… after the first due nag."""
    if nags <= 1:
        return 1
    return min(MAX_NAG_DAYS, 2 ** (nags - 1))


def _describe(item: dict) -> str:
    due = item.get("due", "")[:16].replace("T", " ")
    nags = item.get("nags", 0)
    extra = f", nudged {nags}×" if nags else ""
    return f"[{item['id'][:6]}] {item.get('who','')} — {item.get('what','')} (due {due}{extra})"


def _match(item: dict, ident: str) -> bool:
    ident = ident.lower()
    return (
        item["id"].startswith(ident)
        or ident in (item.get("who") or "").lower()
        or ident in (item.get("what") or "").lower()
    )


def add_followup(who: str, what: str, due_raw: str, now: datetime | None = None) -> str:
    now = now or datetime.now()
    who = (who or "").strip()
    what = (what or "").strip()
    if not who or not what:
        return "A follow-up needs who and what — e.g. recruiter / check if they replied."
    due = parse_due(due_raw, now)
    if due is None:
        return (f"'{due_raw}' is not a date I understand. "
                f"Try Thursday, tomorrow, in 3 days, or YYYY-MM-DD.")
    if due < now - timedelta(minutes=5):
        return f"That due date is already in the past ({due.strftime('%Y-%m-%d %H:%M')})."

    with _lock:
        items = _load()
        open_n = sum(1 for i in items if i.get("status") == "open")
        if open_n >= MAX_OPEN:
            return f"Open follow-up limit reached ({MAX_OPEN}). Close or drop one first."
        item = {
            "id":       uuid.uuid4().hex,
            "who":      who[:80],
            "what":     what[:200],
            "due":      due.strftime("%Y-%m-%dT%H:%M"),
            "status":   "open",
            "nags":     0,
            "last_nag": "",
            "next_nag": due.strftime("%Y-%m-%dT%H:%M"),
            "created":  now.strftime("%Y-%m-%dT%H:%M"),
        }
        items.append(item)
        _save(items)
    return f"Follow-up saved: {_describe(item)}"


def _pick(ident: str, items: list[dict]) -> tuple[dict | None, str]:
    ident = (ident or "").strip()
    if not ident:
        return None, "Say which follow-up — a name, a word from it, or its id."
    hits = [i for i in items if i.get("status") == "open" and _match(i, ident)]
    if not hits:
        return None, f"No open follow-up matches '{ident}'."
    if len(hits) > 1:
        return None, ("That matches several: "
                      + "; ".join(_describe(i) for i in hits)
                      + ". Use the id to pick one.")
    return hits[0], ""


def close_followup(ident: str) -> str:
    with _lock:
        items = _load()
        hit, err = _pick(ident, items)
        if err:
            return err
        hit["status"] = "done"
        hit["closed"] = datetime.now().strftime("%Y-%m-%dT%H:%M")
        _save(items)
    return f"Closed: {_describe(hit)}"


def drop_followup(ident: str) -> str:
    with _lock:
        items = _load()
        hit, err = _pick(ident, items)
        if err:
            return err
        hit["status"] = "dropped"
        hit["closed"] = datetime.now().strftime("%Y-%m-%dT%H:%M")
        _save(items)
    return f"Dropped: {_describe(hit)}"


def snooze_followup(ident: str, days: int = 2, now: datetime | None = None) -> str:
    now = now or datetime.now()
    try:
        days = max(1, min(30, int(days)))
    except (TypeError, ValueError):
        days = 2
    nxt = now + timedelta(days=days)
    with _lock:
        items = _load()
        hit, err = _pick(ident, items)
        if err:
            return err
        hit["next_nag"] = nxt.strftime("%Y-%m-%dT%H:%M")
        _save(items)
    return f"Snoozed {_describe(hit)} until {nxt.strftime('%Y-%m-%d')}."


def list_followups(include_closed: bool = False) -> str:
    items = _load()
    shown = items if include_closed else [i for i in items if i.get("status") == "open"]
    if not shown:
        return "No open follow-ups."
    return "Follow-ups:\n" + "\n".join(_describe(i) for i in shown)


def due_followups(now: datetime | None = None) -> list[dict]:
    """Return open loops that should nag now, and stamp last_nag / next_nag.

    Called every ~60s. Idempotent per calendar day per item."""
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    due: list[dict] = []
    with _lock:
        items = _load()
        changed = False
        for item in items:
            if item.get("status") != "open":
                continue
            last = (item.get("last_nag") or "")[:10]
            if last == today:
                continue
            try:
                nxt = datetime.strptime(item.get("next_nag") or item["due"], "%Y-%m-%dT%H:%M")
            except Exception:
                continue
            if now < nxt:
                continue
            nags = int(item.get("nags") or 0) + 1
            item["nags"] = nags
            item["last_nag"] = now.strftime("%Y-%m-%dT%H:%M")
            nxt2 = now + timedelta(days=_interval_days(nags))
            item["next_nag"] = nxt2.strftime("%Y-%m-%dT%H:%M")
            due.append(dict(item))
            changed = True
        if changed:
            _save(items)
    return due


def overdue_preview(now: datetime | None = None, limit: int = 4) -> list[str]:
    """Read-only list for the proactive prompt — does not stamp nags."""
    now = now or datetime.now()
    out = []
    for item in _load():
        if item.get("status") != "open":
            continue
        try:
            nxt = datetime.strptime(item.get("next_nag") or item["due"], "%Y-%m-%dT%H:%M")
        except Exception:
            continue
        if now >= nxt:
            out.append(_describe(item))
        if len(out) >= limit:
            break
    return out
