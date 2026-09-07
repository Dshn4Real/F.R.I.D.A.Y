"""Headless tests for follow-ups + Spotify media-key routing."""

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import actions.followups as fu
import plugins.spotify as sp
from core.plugin_loader import discover_plugins

PASS = 0


def check(name, cond, detail=""):
    global PASS
    assert cond, f"FAIL: {name} {detail}"
    PASS += 1
    print(f"  ok - {name}")


def fresh():
    fu.FOLLOWUPS_PATH = Path(tempfile.mkdtemp()) / "followups.json"


def test_parse_due():
    now = datetime(2026, 9, 5, 11, 0)  # Saturday
    check("iso date", fu.parse_due("2026-09-10", now) == datetime(2026, 9, 10, 9, 0))
    check("iso datetime", fu.parse_due("2026-09-10 14:30", now) == datetime(2026, 9, 10, 14, 30))
    check("tomorrow", fu.parse_due("tomorrow", now).date() == datetime(2026, 9, 6).date())
    check("in 3 days", fu.parse_due("in 3 days", now).date() == datetime(2026, 9, 8).date())
    check("in 2 hours", fu.parse_due("in 2 hours", now) == datetime(2026, 9, 5, 13, 0))
    thu = fu.parse_due("thursday", now)
    check("this coming thursday", thu.weekday() == 3 and thu.date() == datetime(2026, 9, 10).date())
    check("garbage is None", fu.parse_due("blursday", now) is None)


def test_add_close_list():
    fresh()
    now = datetime(2026, 9, 5, 11, 0)
    check("empty list", "No open" in fu.list_followups())
    r = fu.add_followup("recruiter", "internship reply", "thursday", now)
    check("add ok", "Follow-up saved" in r and "recruiter" in r)
    check("past due rejected", "past" in fu.add_followup("x", "y", "2020-01-01", now))
    check("missing who", "who and what" in fu.add_followup("", "y", "thursday", now))
    lst = fu.list_followups()
    check("list shows open", "recruiter" in lst)
    check("close by name", "Closed" in fu.close_followup("recruiter"))
    check("gone after close", "No open" in fu.list_followups())


def test_spaced_nags():
    fresh()
    now = datetime(2026, 9, 5, 11, 0)
    fu.add_followup("Ali", "send notes", "2026-09-05 11:00", now)
    first = fu.due_followups(now)
    check("first nag on due", len(first) == 1 and first[0]["nags"] == 1)
    check("idempotent same day", fu.due_followups(now) == [])
    next_day = now + timedelta(days=1, hours=1)
    second = fu.due_followups(next_day)
    check("second nag next day", len(second) == 1 and second[0]["nags"] == 2)
    too_soon = next_day + timedelta(hours=2)
    check("spacing blocks early nag", fu.due_followups(too_soon) == [])
    later = next_day + timedelta(days=2)
    third = fu.due_followups(later)
    check("third nag after 2-day gap", len(third) == 1 and third[0]["nags"] == 3)


def test_snooze_and_drop():
    fresh()
    now = datetime(2026, 9, 5, 11, 0)
    fu.add_followup("bank", "invoice", "2026-09-05 11:00", now)
    fu.due_followups(now)
    msg = fu.snooze_followup("bank", 3, now)
    check("snooze message", "Snoozed" in msg)
    check("snooze holds", fu.due_followups(now + timedelta(days=1)) == [])
    check("fires after snooze", len(fu.due_followups(now + timedelta(days=3, hours=1))) == 1)
    fresh()
    fu.add_followup("bank", "invoice", "thursday", now)
    check("drop", "Dropped" in fu.drop_followup("bank"))
    check("dropped not due", fu.due_followups(datetime(2026, 9, 10, 10, 0)) == [])


def test_preview_readonly():
    fresh()
    now = datetime(2026, 9, 5, 11, 0)
    fu.add_followup("Maya", "call back", "2026-09-05 11:00", now)
    prev = fu.overdue_preview(now)
    check("preview lists overdue", any("Maya" in p for p in prev))
    check("preview does not stamp", fu._load()[0]["nags"] == 0)


def test_spotify_media_keys():
    pressed = []
    sp._press_media = lambda vk: pressed.append(vk)
    old_store = sp._store_path
    sp._store_path = lambda: Path(tempfile.mkdtemp()) / "spotify.json"
    try:
        r = sp.run({"action": "pause"})
        check("pause uses media key", pressed == [0xB3], str(pressed))
        check("pause message", "Play/pause" in r)
        sp.run({"action": "next"})
        check("next vk", pressed[-1] == 0xB0)
        st = sp.run({"action": "status"})
        check("status without oauth", "Not connected" in st or "media keys" in st.lower() or "Client ID" in st)
        bad = sp.run({"action": "explode"})
        check("unknown action rejected", "Unknown" in bad)
    finally:
        sp._press_media = None
        sp._store_path = old_store


def test_plugins_discover():
    root = Path(__file__).resolve().parent.parent
    reg = discover_plugins(root / "plugins", core_tool_names=set(), logger=lambda m: None)
    check("follow_up loaded", reg.has("follow_up"))
    check("spotify loaded", reg.has("spotify"))
    names = {r["name"] for r in reg.list_for_ui() if r["valid"]}
    check("both valid in UI list", {"follow_up", "spotify"} <= names)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print(f"\nALL {PASS} CHECKS PASSED ({len(tests)} tests)")
