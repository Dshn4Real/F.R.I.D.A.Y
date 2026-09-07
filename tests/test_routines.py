"""
tests/test_routines.py — routines store + due logic, and the screen_find
coordinate scaling fix. Headless, no network. Run: python tests/test_routines.py
"""

import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import actions.routines as rt

PASS = 0

def check(name, cond, detail=""):
    global PASS
    assert cond, f"FAIL: {name} {detail}"
    PASS += 1
    print(f"  ok - {name}")


def fresh_store():
    rt.ROUTINES_PATH = Path(tempfile.mkdtemp()) / "routines.json"


def test_parsing():
    check("time 9", rt._parse_time("9") == "09:00")
    check("time 9:30", rt._parse_time("9:30") == "09:30")
    check("time 21.05", rt._parse_time("21.05") == "21:05")
    check("time invalid", rt._parse_time("25:00") is None)
    check("days default", rt._parse_days(None) == list(rt._DAY_KEYS))
    check("days weekdays", rt._parse_days("weekdays") == ["mon", "tue", "wed", "thu", "fri"])
    check("days list", rt._parse_days("mon, friday") == ["mon", "fri"])
    check("days invalid", rt._parse_days("blursday") is None)


def test_add_remove_list():
    fresh_store()
    check("empty list", "No routines" in rt.list_routines())
    r = rt.add_routine("9", "open VS Code", "weekdays")
    check("add ok", "Routine saved" in r and "09:00" in r)
    check("bad time rejected", "not a valid time" in rt.add_routine("99", "x"))
    check("empty command rejected", "needs a command" in rt.add_routine("10", " "))
    rt.add_routine("18:00", "weather report", "daily")
    lst = rt.list_routines()
    check("list shows both", "open VS Code" in lst and "weather report" in lst)
    check("ambiguous remove refused", "matches several" not in rt.remove_routine("weather"))
    check("removed", "No routines" not in rt.list_routines())
    check("remove missing", "No routine matches" in rt.remove_routine("zzz"))


def test_due_logic():
    fresh_store()
    rt.add_routine("09:00", "morning routine", "daily")
    rt.add_routine("09:00", "weekday routine", "weekdays")

    # Friday 09:05 — inside grace window, both day filters match.
    fri = datetime(2026, 9, 4, 9, 5)
    due = rt.due_routines(fri)
    check("both due in window", sorted(d["command"] for d in due)
          == ["morning routine", "weekday routine"])
    check("idempotent same day", rt.due_routines(fri) == [])

    # Saturday 09:05 — new day, weekday routine must not fire.
    sat = datetime(2026, 9, 5, 9, 5)
    due = rt.due_routines(sat)
    check("weekend filters weekday routine", [d["command"] for d in due]
          == ["morning routine"])

    # Sunday 09:20 — past the 10-minute grace window: skipped, not queued.
    sun = datetime(2026, 9, 6, 9, 20)
    check("stale routine skipped", rt.due_routines(sun) == [])
    # And 08:55 — before schedule: not due.
    mon = datetime(2026, 9, 7, 8, 55)
    check("early check not due", rt.due_routines(mon) == [])


def test_screen_find_scaling():
    import actions.computer_control as cc
    from PIL import Image
    from google import genai

    class FakePG:
        FAILSAFE = True
        @staticmethod
        def size(): return (1920, 1080)                 # logical
        @staticmethod
        def screenshot(): return Image.new("RGB", (2880, 1620))  # 150% DPI

    answers = {}
    class FakeModels:
        def generate_content(self, **kw):
            class R: text = answers["text"]
            return R()
    class FakeClient:
        def __init__(self, api_key): self.models = FakeModels()

    old_pg, old_client, old_flag = cc.pyautogui, genai.Client, cc._PYAUTOGUI
    old_key = cc._get_api_key
    cc.pyautogui, cc._PYAUTOGUI = FakePG, True
    cc._get_api_key = lambda: "fake"
    genai.Client = FakeClient
    try:
        answers["text"] = "500,500"       # normalized centre
        check("normalized scales to screen centre",
              cc._screen_find("the button") == (960, 540))
        answers["text"] = "2880,1620"     # model answered in image pixels
        x, y = cc._screen_find("corner")
        check("pixel answer rescaled+clamped to logical space",
              (x, y) == (1919, 1079), f"{x},{y}")
        answers["text"] = "NOT_FOUND"
        check("not found is None", cc._screen_find("ghost") is None)
    finally:
        cc.pyautogui, cc._PYAUTOGUI = old_pg, old_flag
        cc._get_api_key = old_key
        genai.Client = old_client


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print(f"\nALL {PASS} CHECKS PASSED ({len(tests)} tests)")
