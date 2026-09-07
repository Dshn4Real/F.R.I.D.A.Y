"""WhatsApp Call plugin — outbound voice & video via WhatsApp Desktop."""

from __future__ import annotations

import re

from core import confirm as confirm_gate

PLUGIN = {
    "name": "whatsapp_call",
    "description": (
        "Place a WhatsApp voice or video call to a contact using WhatsApp Desktop. "
        "Use when the user says call / video call / ring someone on WhatsApp. "
        "Do NOT use send_message for calls. Do NOT use browser_control."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "contact": {
                "type": "STRING",
                "description": "Contact name as saved in WhatsApp (e.g. Mom, Ali).",
            },
            "mode": {
                "type": "STRING",
                "description": "voice (default) | video",
            },
        },
        "required": ["contact"],
    },
}


def banner_copy(contact: str, *, video: bool) -> tuple[str, str]:
    label = "video call" if video else "voice call"
    return f"WhatsApp {label}", f"Call {contact} on WhatsApp?"


def parse_banner(title: str, detail: str) -> tuple[str, str] | None:
    """Return (contact, voice|video) when this confirm is an outbound WhatsApp call."""
    t = (title or "").strip().lower()
    if "whatsapp" not in t or "call" not in t:
        return None
    kind = "video" if "video" in t else "voice"
    contact = ""
    m = re.search(r"(?i)call\s+(.+?)\s+on\s+whatsapp", detail or "")
    if m:
        contact = m.group(1).strip().rstrip("?").strip()
    return (contact or "Contact", kind)


def caller_from_title(title: str) -> str:
    t = (title or "").strip()
    if not t:
        return "WhatsApp"
    low = t.lower()
    for needle in (" is calling", " calling you", " incoming"):
        i = low.find(needle)
        if i > 0:
            name = t[:i].strip()
            return name or "WhatsApp"
    if low in ("whatsapp", "incoming", "incoming call", "voice call", "video call"):
        return "WhatsApp"
    return t


def run(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    contact = str(params.get("contact") or "").strip()
    mode = str(params.get("mode") or "voice").strip().lower()
    if not contact:
        return "Please tell me who to call on WhatsApp."

    video = mode in ("video", "facetime", "camera")
    label = "video call" if video else "voice call"

    def _do() -> str:
        from actions._whatsapp_ui import start_video_call, start_voice_call

        if player:
            try:
                player.write_log(f"WA: starting {label} → {contact}")
            except Exception:
                pass
        try:
            return start_video_call(contact) if video else start_voice_call(contact)
        except Exception as e:
            return f"WhatsApp {label} failed: {e}"

    title, detail = banner_copy(contact, video=video)
    return confirm_gate.request(
        key=f"wa_call:{contact}:{mode}",
        title=title,
        detail=detail,
        run=_do,
    )
