"""Headless tests for generic chat automation — no live WhatsApp/Instagram."""
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import actions.chat_watch as cw
from actions._whatsapp_ui import looks_like_group
from core.plugin_loader import discover_plugins


def test_classify_and_normalize():
    assert cw.classify_surface("Mom", "WhatsApp.exe") == "whatsapp"
    assert cw.classify_surface("Instagram • Direct", "chrome.exe") == "instagram"
    assert cw.classify_surface("Inbox • Instagram and 2 more pages", "msedge.exe") == "instagram"
    assert cw.classify_surface("Telegram") == "telegram"
    assert cw.normalize_app("ig") == "instagram"
    assert cw.normalize_app("insta") == "instagram"
    assert cw.normalize_app("auto") == "auto"
    assert cw.normalize_app("") == "auto"
    assert cw.normalize_app("wa") == "whatsapp"


def test_incoming_parse():
    snippet = "OUT: hey\nIN: you free?\nIN: hello"
    assert cw.last_incoming(snippet) == "hello"
    assert cw.last_incoming("just a raw line") == "just a raw line"
    assert cw.snippet_fingerprint("Hey  there") == cw.snippet_fingerprint("hey there")
    noisy = "IN: 10:42\nIN: hello there\nOUT: ok\nIN: Delivered"
    assert cw.last_incoming(noisy) == "hello there"
    assert cw.last_incoming("OUT: only us") == ""
    assert cw.last_incoming("IN: Ask Meta AI") == ""


def test_dpi_scale_virtualized():
    from actions._whatsapp_ui import dpi_scale_from

    assert dpi_scale_from(120, 96) == 1.25
    assert dpi_scale_from(144, 96) == 1.5
    assert dpi_scale_from(120, 120) == 1.0
    assert dpi_scale_from(96, 96) == 1.0


def test_thread_crop_keeps_bottom():
    left, top, w, h = cw._thread_crop((500, 0, 1000, 1000))
    assert left == 500
    assert w == 1000
    assert top > 200
    assert top + h < 950


def test_group_guard_whatsapp_only():
    assert looks_like_group("Family Group")
    msg = cw.start("Family Group", app="whatsapp")
    assert "group" in msg.lower()


def test_start_on_open_instagram_window():
    prev_req = cw.wa._require
    old_path = cw._PREFS_PATH
    cw._PREFS_PATH = Path(tempfile.mkdtemp()) / "chat.json"
    cw._detect_hook = lambda prefer: {
        "hwnd": 42,
        "title": "Instagram • Direct",
        "app": "instagram",
        "left": 0,
        "top": 0,
        "width": 900,
        "height": 700,
        "pid": 1,
    }
    cw._read_hook = lambda *a: "IN: hey from ig"
    cw._send_hook = lambda *a: "ok"
    cw._draft_hook = lambda incoming: f"re:{incoming[:20]}"
    cw.wa._require = lambda: None
    try:
        # group guard returns before _require for whatsapp; instagram + hook
        # should start without opening a browser.
        msg = cw.start("", app="instagram", draft_only=True)
        assert "Instagram" in msg or "started" in msg.lower()
        assert cw.load_prefs().get("chat_app") == "instagram"
        assert "stopped" in cw.stop().lower()
        assert cw.load_prefs().get("chat_active") is False
    finally:
        cw.stop()
        cw._detect_hook = None
        cw._read_hook = None
        cw._send_hook = None
        cw._draft_hook = None
        cw.wa._require = prev_req
        cw._PREFS_PATH = old_path


def test_plugin_status_includes_app():
    reg = discover_plugins(ROOT / "plugins", set(), logger=lambda *_: None)
    chat = reg.run("chat_automation", {"action": "status"})
    assert "chat_active=" in chat
    assert "app=" in chat
    ig = reg.run("chat_automation", {"action": "start", "contact": "Family Group"})
    assert "group" in ig.lower()


if __name__ == "__main__":
    test_classify_and_normalize()
    test_incoming_parse()
    test_dpi_scale_virtualized()
    test_thread_crop_keeps_bottom()
    test_group_guard_whatsapp_only()
    test_start_on_open_instagram_window()
    test_plugin_status_includes_app()
    print("ok")
