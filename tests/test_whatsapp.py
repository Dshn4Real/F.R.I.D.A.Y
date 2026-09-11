"""Smoke tests for WhatsApp plugins — no live WhatsApp required."""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from actions._whatsapp_ui import (
    contact_list_item_re,
    looks_like_group,
    search_edit_kind,
    _hwnds_whatsapp,
    _win32_windows,
)
from core.plugin_loader import discover_plugins
from plugins.whatsapp_call import banner_copy, caller_from_title, parse_banner


def test_group_guard():
    assert looks_like_group("Family Group")
    assert looks_like_group("Ali, Sara")
    assert not looks_like_group("Saharsh")
    assert not looks_like_group("Mom")


def test_window_enum_does_not_crash():
    wins = _win32_windows()
    assert isinstance(wins, list)
    hwnds = _hwnds_whatsapp()
    assert isinstance(hwnds, list)


def test_contact_list_item_re():
    pattern = contact_list_item_re("Saharsh")
    assert re.match(pattern, "Saharsh")
    assert re.match(pattern, "Saharsh\nlast message")
    assert not re.match(pattern, "Saharsh Uncle")
    assert not re.match(pattern, "Hey Saharsh")


def test_search_edit_kind():
    assert search_edit_kind("Search favourite chats") == "favourites"
    assert search_edit_kind("Search favorite chats") == "favourites"
    assert search_edit_kind("Search or start a new chat") == "all"
    assert search_edit_kind("Search name, number or @username") == "all"
    assert search_edit_kind("") == ""
    assert search_edit_kind("Type a message") == ""


def test_win32_call_keys_mapped():
    from actions._whatsapp_ui import _VK

    for key in ("enter", "down", "space", "ctrl", "a", "backspace"):
        assert key in _VK


def test_call_banner_copy():
    title, detail = banner_copy("Saharsh", video=False)
    parsed = parse_banner(title, detail)
    assert parsed == ("Saharsh", "voice")
    title, detail = banner_copy("Mom", video=True)
    assert parse_banner(title, detail) == ("Mom", "video")
    assert parse_banner("Shut down", "Turn the PC off?") is None
    assert caller_from_title("Saharsh is calling") == "Saharsh"
    assert caller_from_title("WhatsApp") == "WhatsApp"


def test_plugins_discover_and_status():
    reg = discover_plugins(ROOT / "plugins", set(), logger=lambda *_: None)
    names = {r["name"] for r in reg.list_for_ui() if r["valid"]}
    assert {"whatsapp_call", "manage_calls", "chat_automation"} <= names
    status = reg.run("manage_calls", {"action": "status"})
    assert "armed=" in status
    chat = reg.run("chat_automation", {"action": "status"})
    assert "chat_active=" in chat
    missing = reg.run("whatsapp_call", {"contact": ""})
    assert "who" in missing.lower() or "tell me" in missing.lower()
    group = reg.run("chat_automation", {"action": "start", "contact": "Family Group"})
    assert "group" in group.lower()
    draft_flag = reg.run("chat_automation", {"action": "set_draft_only", "draft_only": "false"})
    assert "OFF" in draft_flag
    draft_on = reg.run("chat_automation", {"action": "set_draft_only", "draft_only": "true"})
    assert "ON" in draft_on


if __name__ == "__main__":
    test_group_guard()
    test_window_enum_does_not_crash()
    test_contact_list_item_re()
    test_search_edit_kind()
    test_win32_call_keys_mapped()
    test_call_banner_copy()
    test_plugins_discover_and_status()
    print("ok")
