"""
Generic chat automation — watch the chat the user already has open.

WhatsApp Desktop is one adapter. Instagram (instagram.com in the user's own
browser) is another. Anything else with a chat-shaped window falls back to
UI Automation + a screenshot read, so "chat for me" works on the foreground
app instead of hard-coding one messenger.
"""
from __future__ import annotations

import io
import os
import re
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any, Optional

from actions import _whatsapp_ui as wa
from actions._whatsapp_ui import (
    copy_latest_chat_snippet,
    looks_like_group,
    open_chat,
    send_chat_text,
)
from core.secure_store import read_json, write_json

_BASE = Path(__file__).resolve().parent.parent
_PREFS_PATH = _BASE / "data" / "chat_automation.json"
_PREFS_LOCK = threading.Lock()

_DEFAULT: dict[str, Any] = {
    "chat_active": False,
    "chat_app": "",
    "chat_contact": "",
    "chat_persona": "polite, brief, natural. Match the user's language.",
    "chat_draft_only": True,
    "chat_max_replies": 20,
    "chat_replies_sent": 0,
    "chat_min_interval_sec": 3,
    "last_sent_reply": "",
    "chat_hwnd": 0,
    "chat_title": "",
}

_stop = threading.Event()
_thread: Optional[threading.Thread] = None

# Tests replace these so we never drive a real window.
_read_hook = None
_send_hook = None
_detect_hook = None
_draft_hook = None

_SKIP_TITLE = (
    "friday", "cursor", "visual studio", "pycharm", "task manager",
    "program manager", "settings",
)

_APP_KEYS = (
    ("whatsapp", ("whatsapp",)),
    ("instagram", ("instagram",)),
    ("messenger", ("messenger",)),
    ("telegram", ("telegram",)),
    ("discord", ("discord",)),
    ("imessage", ("imessage", "messages")),
)

_COMPOSER = {
    "whatsapp": (0.62, 0.93),
    "instagram": (0.56, 0.93),
    "messenger": (0.55, 0.93),
    "telegram": (0.55, 0.94),
    "discord": (0.50, 0.94),
    "generic": (0.55, 0.93),
}

_INSTAGRAM_INBOX = "https://www.instagram.com/direct/inbox/"


def classify_surface(title: str, process: str = "") -> str:
    blob = f"{title or ''} {process or ''}".lower()
    for name, keys in _APP_KEYS:
        if any(k in blob for k in keys):
            return name
    return "generic"


def normalize_app(app: str) -> str:
    raw = (app or "").strip().lower()
    if raw in ("", "auto", "open", "current", "foreground", "this", "whatever"):
        return "auto"
    if raw in ("ig", "insta", "instagram.com"):
        return "instagram"
    if raw in ("wa", "whatsapp desktop"):
        return "whatsapp"
    return classify_surface(raw, raw)


def load_prefs() -> dict[str, Any]:
    with _PREFS_LOCK:
        data = dict(_DEFAULT)
        if _PREFS_PATH.is_file():
            try:
                raw = read_json(_PREFS_PATH)
                if isinstance(raw, dict):
                    data.update(raw)
            except Exception:
                pass
        return data


def save_prefs(**fields: Any) -> dict[str, Any]:
    with _PREFS_LOCK:
        data = dict(_DEFAULT)
        if _PREFS_PATH.is_file():
            try:
                raw = read_json(_PREFS_PATH)
                if isinstance(raw, dict):
                    data.update(raw)
            except Exception:
                pass
        data.update(fields)
        _PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        write_json(_PREFS_PATH, data)
        return data


def _api_key() -> str:
    try:
        from memory.config_manager import get_gemini_key
        return get_gemini_key() or ""
    except Exception:
        return ""


def _foreground() -> dict[str, Any]:
    if os.name != "nt":
        return {}
    import ctypes

    hwnd = int(ctypes.windll.user32.GetForegroundWindow())
    if not hwnd:
        return {}
    for w in wa._win32_windows(visible_only=False):
        if int(w.get("hwnd") or 0) == hwnd:
            return w
    return {"hwnd": hwnd, "title": "", "width": 0, "height": 0}


def _process_name(pid: int) -> str:
    if not pid:
        return ""
    try:
        import psutil

        return (psutil.Process(int(pid)).name() or "").lower()
    except Exception:
        return ""


def _is_skip_title(title: str) -> bool:
    t = (title or "").lower()
    return any(k in t for k in _SKIP_TITLE)


def _hwnd_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    if not hwnd:
        return None
    try:
        phys = wa.physical_hwnd_rect(int(hwnd))
        if phys:
            return phys
    except Exception:
        pass
    if os.name != "nt":
        return None
    import ctypes

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    rc = RECT()
    if not ctypes.windll.user32.GetWindowRect(int(hwnd), ctypes.byref(rc)):
        return None
    w, h = int(rc.right - rc.left), int(rc.bottom - rc.top)
    if w < 80 or h < 80:
        return None
    return int(rc.left), int(rc.top), w, h


def _log(player, msg: str) -> None:
    print(f"[ChatWatch] {msg}")
    if player:
        try:
            player.write_log(f"SYS: {msg}")
        except Exception:
            pass


def _com_init() -> None:
    try:
        import pythoncom

        pythoncom.CoInitialize()
    except Exception:
        pass


def detect_chat_window(prefer: str = "auto") -> dict[str, Any]:
    """Pick the chat window to drive. Tests can replace this via _detect_hook."""
    if _detect_hook is not None:
        return _detect_hook(prefer)
    prefer = normalize_app(prefer)
    wins = wa._win32_windows(visible_only=False) if os.name == "nt" else []
    wa_hwnds = set()
    try:
        wa_hwnds = set(int(h) for h in wa._hwnds_whatsapp())
    except Exception:
        wa_hwnds = set()
    scored: list[tuple[int, dict]] = []
    for w in wins:
        title = str(w.get("title") or "")
        hwnd = int(w.get("hwnd") or 0)
        if _is_skip_title(title) and hwnd not in wa_hwnds:
            continue
        width, height = int(w.get("width") or 0), int(w.get("height") or 0)
        if width < 360 or height < 360:
            continue
        proc = _process_name(int(w.get("pid") or 0))
        kind = classify_surface(title, proc)
        if hwnd in wa_hwnds:
            kind = "whatsapp"
        if not title and kind == "generic":
            continue
        if prefer not in ("auto", "generic") and kind != prefer:
            continue
        if prefer == "auto" and kind == "generic":
            continue
        scored.append((width * height, {**w, "app": kind, "process": proc}))
    scored.sort(key=lambda x: x[0], reverse=True)
    if scored:
        return scored[0][1]
    fg = _foreground()
    title = str(fg.get("title") or "")
    hwnd = int(fg.get("hwnd") or 0)
    if fg and (hwnd in wa_hwnds or not _is_skip_title(title)):
        proc = _process_name(int(fg.get("pid") or 0))
        kind = "whatsapp" if hwnd in wa_hwnds else classify_surface(title, proc)
        return {**fg, "app": kind, "process": proc}
    return {}


def _focus_target(hwnd: int) -> bool:
    if not hwnd:
        return False
    return bool(wa._force_foreground(int(hwnd)))


def _uia_read(hwnd: int, app: str = "") -> str:
    if not hwnd:
        return ""
    pane_left = None
    pane_bottom = None
    if app == "whatsapp":
        try:
            pane = wa._chat_pane_rect()
            if pane:
                pane_left = int(pane[0])
                pane_bottom = int(pane[1] + pane[3])
        except Exception:
            pane_left = None
    try:
        from pywinauto import Application, Desktop

        win = None
        try:
            app_uia = Application(backend="uia").connect(handle=int(hwnd), timeout=2)
            win = app_uia.window(handle=int(hwnd))
        except Exception:
            win = Desktop(backend="uia").window(handle=int(hwnd))
        wr = win.rectangle()
        mid = (wr.left + wr.right) * 0.48
        chunks: list[str] = []
        try:
            items = list(win.descendants(control_type="DataItem")[-12:])
            if not items:
                items = list(win.descendants(control_type="ListItem")[-10:])
            for item in items:
                try:
                    r = item.rectangle()
                    if pane_left is not None:
                        if r.right < pane_left + 8:
                            continue
                        if pane_bottom is not None and r.top > pane_bottom:
                            continue
                    elif r.left < mid:
                        continue
                except Exception:
                    pass
                t = (item.window_text() or "").strip()
                if t and len(t) > 1:
                    chunks.append(t)
        except Exception:
            pass
        if chunks:
            return "\n".join(chunks[-5:])
        texts: list[str] = []
        try:
            for c in win.descendants(control_type="Text")[-16:]:
                try:
                    r = c.rectangle()
                    if pane_left is not None:
                        if r.right < pane_left + 8:
                            continue
                    elif r.left < mid:
                        continue
                except Exception:
                    pass
                t = (c.window_text() or "").strip()
                if t and len(t) > 1:
                    texts.append(t)
        except Exception:
            pass
        if texts:
            return "\n".join(texts[-6:])
    except Exception:
        return ""
    return ""


def _thread_crop(pane: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Keep the lower thread, drop header + composer so the newest IN bubble is in frame."""
    left, top, width, height = pane
    header = max(56, int(height * 0.08))
    composer = max(70, int(height * 0.11))
    body_t = top + header
    body_h = max(80, height - header - composer)
    crop_t = body_t + int(body_h * 0.36)
    crop_h = body_h - int(body_h * 0.36)
    return left, crop_t, width, max(80, crop_h)


def _pane_crop(app: str, hwnd: int, rect: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    live = _hwnd_rect(hwnd) or rect
    left, top, width, height = live
    if app == "whatsapp":
        try:
            pane = wa._chat_pane_rect()
            if pane:
                return _thread_crop(pane)
        except Exception:
            pass
    # Sidebar is the left third on WhatsApp / Instagram Direct.
    crop_l = left + int(width * 0.34)
    crop_t = top + int(height * 0.12)
    crop_w = int(width * 0.64)
    crop_h = int(height * 0.72)
    return _thread_crop((crop_l, crop_t, crop_w, crop_h))


def _vision_read(rect: tuple[int, int, int, int], app: str, hwnd: int = 0) -> str:
    key = _api_key()
    if not key:
        return ""
    crop_l, crop_t, crop_w, crop_h = _pane_crop(app, hwnd, rect)
    if crop_w < 80 or crop_h < 80:
        return ""
    try:
        from google import genai
        from google.genai import types as gtypes
        from PIL import Image

        arr = None
        try:
            arr = wa._grab_region(crop_l, crop_t, crop_w, crop_h)
        except Exception:
            arr = None
        buf = io.BytesIO()
        if arr is not None:
            # mss is BGRA; ImageGrab is RGB.
            if arr.ndim == 3 and arr.shape[2] >= 3:
                if arr.shape[2] == 4:
                    rgb = arr[:, :, :3][:, :, ::-1]
                else:
                    rgb = arr
                Image.fromarray(rgb).save(buf, format="PNG")
            else:
                return ""
        else:
            if not wa._ensure_pya() or wa.pyautogui is None:
                return ""
            img = wa.pyautogui.screenshot(region=(crop_l, crop_t, crop_w, crop_h))
            img.save(buf, format="PNG")
        client = genai.Client(api_key=key)
        prompt = (
            f"This is a {app or 'chat'} conversation screenshot. "
            "The NEWEST messages are at the BOTTOM. "
            "List the LAST 4 visible message bubbles, newest last. "
            "One message per line as: IN: text   or   OUT: text. "
            "IN = the other person (left-side bubbles). "
            "OUT = us (right-side green/blue bubbles). "
            "Copy the wording, do not summarize. "
            "Ignore the left chat list, header name, timestamps-only, "
            "'Ask Meta AI', 'Type a message', Delivered/Read. "
            "If no thread is open (empty home with Ask Meta AI), reply exactly NO_THREAD. "
            "If this is not a chat, reply exactly NOT_CHAT."
        )
        resp = client.models.generate_content(
            model="gemini-flash-lite-latest",
            contents=[
                gtypes.Part.from_bytes(data=buf.getvalue(), mime_type="image/png"),
                prompt,
            ],
        )
        text = (getattr(resp, "text", None) or "").strip()
        upper = text.upper()
        if not text or "NOT_CHAT" in upper or "NO_THREAD" in upper:
            return ""
        return text[:1500]
    except Exception as e:
        print(f"[ChatWatch] vision read failed: {e}")
        return ""


_NOISE_RE = re.compile(
    r"^(delivered|read|seen|forwarded|online|typing[.…] *|whatsapp|"
    r"ask meta ai|send document|add contact|type a message|"
    r"pinned chat|message|today|yesterday|new chat|"
    r"\d{1,2}:\d{2}(\s*[ap]m)?)$",
    re.I,
)


def last_incoming(snippet: str) -> str:
    """Newest IN: line, or the last non-OUT line of a raw snippet."""
    lines = [ln.strip() for ln in (snippet or "").splitlines() if ln.strip()]
    incoming = []
    for ln in lines:
        raw = ln
        if ln.upper().startswith("OUT:"):
            continue
        if ln.upper().startswith("IN:"):
            raw = ln.split(":", 1)[1].strip()
        raw = raw.strip().strip('"').strip("'")
        if not raw or len(raw) < 2:
            continue
        if _NOISE_RE.match(raw):
            continue
        incoming.append(raw)
    return incoming[-1] if incoming else ""


def snippet_fingerprint(snippet: str) -> str:
    return re.sub(r"\s+", " ", (snippet or "").strip().lower())[:400]


def read_chat(app: str, hwnd: int, rect: tuple[int, int, int, int]) -> str:
    if _read_hook is not None:
        return _read_hook(app, hwnd, rect)

    def _do() -> str:
        if app == "whatsapp":
            try:
                wa._focus_whatsapp_window()
                time.sleep(0.12)
            except Exception:
                pass
        elif hwnd:
            _focus_target(hwnd)
            time.sleep(0.12)
        vision = _vision_read(rect, app, hwnd)
        if vision:
            return vision
        if app == "whatsapp":
            text = copy_latest_chat_snippet()
            if text:
                return text
        return _uia_read(hwnd, app)

    if app == "whatsapp":
        try:
            with wa.whatsapp_front():
                return _do()
        except Exception:
            return _do()
    return _do()


def draft_reply(incoming: str, *, persona: str = "", app: str = "", last_ours: str = "") -> str:
    incoming = (incoming or "").strip()
    if not incoming:
        return ""
    if last_ours and last_ours.strip() and last_ours.strip() in incoming and len(incoming) < len(last_ours) + 8:
        return ""
    if _draft_hook is not None:
        return _draft_hook(incoming) or ""
    key = _api_key()
    if not key:
        return ""
    try:
        from google import genai

        style = persona or load_prefs().get("chat_persona") or "polite and brief"
        where = app or "this chat app"
        prompt = (
            f"You are texting on {where} on behalf of the user.\n"
            f"Style: {style}\n"
            "Write ONE short reply (1-2 sentences). No quotes, no preamble, "
            "no emoji overload, no hashtags.\n"
            "If the incoming text is already OUR previous reply, output exactly SKIP.\n\n"
            f"Incoming:\n{incoming[:1500]}"
        )
        resp = genai.Client(api_key=key).models.generate_content(
            model="gemini-flash-latest", contents=prompt
        )
        text = (getattr(resp, "text", None) or "").strip()
        line = text.split("\n")[0].strip().strip('"').strip("'")
        if not line or line.upper() == "SKIP":
            return ""
        return line[:280]
    except Exception as e:
        print(f"[ChatWatch] draft failed: {e}")
        return ""


def _click_composer(rect: tuple[int, int, int, int], app: str) -> None:
    if wa.pyautogui is None:
        return
    left, top, width, height = rect
    rx, ry = _COMPOSER.get(app, _COMPOSER["generic"])
    wa.pyautogui.click(left + int(width * rx), top + int(height * ry))
    time.sleep(0.12)


def send_reply(text: str, app: str, hwnd: int, rect: tuple[int, int, int, int]) -> str:
    if _send_hook is not None:
        return _send_hook(text, app, hwnd, rect)
    text = (text or "").strip()
    if not text:
        return "Empty message."
    if app == "whatsapp":
        return send_chat_text(text)
    err = wa._require()
    if err:
        return err
    try:
        if hwnd:
            _focus_target(hwnd)
        time.sleep(0.15)
        _click_composer(rect, app)
        wa._paste(text)
        time.sleep(0.12)
        if wa.pyautogui is not None:
            wa.pyautogui.press("enter")
        time.sleep(0.2)
        return "ok"
    except Exception as e:
        return wa._failsafe_msg(e) or f"Could not send: {e}"


def _wait_for_app(app: str, timeout: float = 8.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = detect_chat_window(app)
        if last.get("hwnd") and last.get("app") == app:
            return last
        if last.get("hwnd") and app == "auto":
            return last
        time.sleep(0.4)
    return last


def _open_instagram(contact: str) -> str:
    """Open Instagram DMs in the user's real browser (logged-in profile)."""
    err = wa._require()
    if err:
        return err
    try:
        if os.name == "nt":
            os.startfile(_INSTAGRAM_INBOX)  # type: ignore[attr-defined]
        else:
            webbrowser.open(_INSTAGRAM_INBOX)
    except Exception:
        if not webbrowser.open(_INSTAGRAM_INBOX):
            return "Could not open Instagram in your browser."
    found = _wait_for_app("instagram", timeout=10.0)
    if not found.get("hwnd"):
        return (
            "I opened Instagram Direct. Switch to that tab if it is behind another "
            "window, then ask me again — or keep the DM open and say chat for me."
        )
    hwnd = int(found["hwnd"])
    _focus_target(hwnd)
    time.sleep(1.2)
    contact = (contact or "").strip()
    if not contact:
        return "ok:instagram"
    rect = (
        int(found.get("left") or 0),
        int(found.get("top") or 0),
        int(found.get("width") or 0),
        int(found.get("height") or 0),
    )
    try:
        from actions.computer_control import _screen_find, _click

        for desc in (
            "the Search box in Instagram Direct inbox",
            "the New message or compose icon",
            "the search field that says Search",
        ):
            coords = _screen_find(desc)
            if coords:
                _click(x=coords[0], y=coords[1])
                time.sleep(0.25)
                break
        else:
            _click_composer(rect, "instagram")
            if wa.pyautogui is not None:
                wa.pyautogui.hotkey("ctrl", "k")
            time.sleep(0.25)
        wa._paste(contact)
        time.sleep(1.1)
        hit = _screen_find(f"the search result named {contact}")
        if hit:
            _click(x=hit[0], y=hit[1])
            time.sleep(0.4)
        elif wa.pyautogui is not None:
            wa.pyautogui.press("enter")
            time.sleep(0.35)
        nxt = _screen_find("the Next or Chat button")
        if nxt:
            _click(x=nxt[0], y=nxt[1])
            time.sleep(0.4)
    except Exception as e:
        print(f"[ChatWatch] Instagram search: {e}")
    return f"ok:{contact}"


def _prepare(app: str, contact: str) -> tuple[str, dict[str, Any]]:
    """Open / focus the surface. Returns (status, window)."""
    if _detect_hook is not None:
        win = detect_chat_window(app)
        return ("ok", win) if win.get("hwnd") else ("no chat window", {})
    if app == "auto" and contact:
        r = open_chat(contact)
        if r.startswith("ok"):
            win = detect_chat_window("whatsapp")
            if win.get("hwnd"):
                return "ok", win
    if app == "whatsapp":
        if contact:
            r = open_chat(contact)
            if not r.startswith("ok"):
                return r, {}
        win = detect_chat_window("whatsapp")
        if not win.get("hwnd"):
            return "WhatsApp is not open. Open it and try again.", {}
        return "ok", win
    if app == "instagram":
        r = _open_instagram(contact)
        if not r.startswith("ok"):
            return r, {}
        win = detect_chat_window("instagram")
        if not win.get("hwnd"):
            return r, {}
        return "ok", win
    win = detect_chat_window(app)
    if not win.get("hwnd"):
        return (
            "I could not find a chat window. Open WhatsApp or Instagram Direct "
            "and leave that chat in front, then say chat for me.",
            {},
        )
    _focus_target(int(win["hwnd"]))
    return "ok", win


def start(
    contact: str = "",
    *,
    app: str = "auto",
    persona: str = "",
    draft_only: bool = True,
    player=None,
) -> str:
    global _thread
    contact = (contact or "").strip()
    app = normalize_app(app)
    if contact and looks_like_group(contact) and app != "instagram":
        return "I will not auto-chat in a group. Name one person, or open their chat."
    err = wa._require()
    if err:
        return err
    if _thread and _thread.is_alive():
        prefs = load_prefs()
        return (
            f"Chat automation is already running on {prefs.get('chat_app') or 'a chat'} "
            f"({prefs.get('chat_contact') or 'open chat'}). "
            "Say stop chat automation first."
        )

    status, win = _prepare(app, contact)
    if not status.startswith("ok"):
        return status
    bound = win.get("app") or app
    if bound in ("auto", ""):
        bound = "generic"
    hwnd = int(win.get("hwnd") or 0)
    rect = (
        int(win.get("left") or 0),
        int(win.get("top") or 0),
        int(win.get("width") or 0),
        int(win.get("height") or 0),
    )
    who = contact or str(win.get("title") or "open chat")
    save_prefs(
        chat_active=True,
        chat_app=bound,
        chat_contact=who,
        chat_persona=persona or load_prefs().get("chat_persona"),
        chat_draft_only=bool(draft_only),
        chat_replies_sent=0,
        last_sent_reply="",
        chat_hwnd=hwnd,
        chat_title=str(win.get("title") or ""),
    )
    _stop.clear()

    def _act(incoming: str, snippet: str, hwnd: int, rect: tuple, bound: str, prefs: dict) -> str:
        last_ours = str(prefs.get("last_sent_reply") or "").strip()
        reply = draft_reply(
            incoming or snippet,
            persona=str(prefs.get("chat_persona") or ""),
            app=bound,
            last_ours=last_ours,
        )
        if not reply or reply == last_ours:
            _log(player, "No new reply drafted.")
            return ""
        label = prefs.get("chat_contact") or bound
        if prefs.get("chat_draft_only"):
            _log(player, f"DRAFT→{label}: {reply}")
            if player and hasattr(player, "show_content"):
                try:
                    player.show_content(f"DRAFT — {label}", reply)
                except Exception:
                    pass
            return reply
        sent = send_reply(reply, bound, hwnd, rect)
        if sent != "ok":
            _log(player, f"Send failed: {sent}")
            return ""
        n = int(prefs.get("chat_replies_sent") or 0) + 1
        save_prefs(chat_replies_sent=n, last_sent_reply=reply)
        _log(player, f"{bound}→{label}: {reply}")
        return reply

    def _loop():
        nonlocal hwnd, rect, bound
        _com_init()
        _log(player, f"Watching {bound} — {who}")
        last_fp = ""
        last_in = ""
        primed = False
        empty = 0
        while True:
            try:
                prefs = load_prefs()
                if not prefs.get("chat_active"):
                    break
                if int(prefs.get("chat_replies_sent") or 0) >= int(prefs.get("chat_max_replies") or 20):
                    save_prefs(chat_active=False)
                    _log(player, "Hit max replies — stopped.")
                    break
                live = detect_chat_window(bound if bound != "generic" else "auto")
                if live.get("hwnd"):
                    hwnd = int(live["hwnd"])
                    bound = live.get("app") or bound
                live_rect = _hwnd_rect(hwnd)
                if live_rect:
                    rect = live_rect
                if bound == "whatsapp" and who and who.lower() not in ("open chat",):
                    if empty >= 1:
                        try:
                            opened = open_chat(who)
                            _log(player, f"Re-opening {who}: {opened}")
                        except Exception as e:
                            _log(player, f"Re-open failed: {e}")
                snippet = read_chat(bound, hwnd, rect)
                incoming = last_incoming(snippet)
                if not snippet:
                    empty += 1
                    if empty == 1 or empty % 4 == 0:
                        _log(player, "Can't read the open chat yet. Keep the conversation visible.")
                    if _stop.wait(float(prefs.get("chat_min_interval_sec") or 3)):
                        break
                    continue
                empty = 0
                fp = snippet_fingerprint(snippet)
                if not primed:
                    primed = True
                    last_fp = fp
                    last_in = incoming
                    if incoming:
                        _log(player, f"Latest: {incoming[:80]}")
                        _act(incoming, snippet, hwnd, rect, bound, prefs)
                    else:
                        _log(player, "Chat is open. Waiting for an incoming message.")
                elif incoming and incoming != last_in:
                    last_ours = str(prefs.get("last_sent_reply") or "").strip()
                    if not (last_ours and last_ours in incoming):
                        _log(player, f"Latest: {incoming[:80]}")
                        _act(incoming, snippet, hwnd, rect, bound, prefs)
                    last_fp = fp
                    last_in = incoming
                else:
                    last_fp = fp
                    if incoming:
                        last_in = incoming
            except Exception as e:
                _log(player, f"Watch error: {e}")
            if _stop.wait(float(load_prefs().get("chat_min_interval_sec") or 3)):
                break

    _thread = threading.Thread(target=_loop, name="friday-chat-watch", daemon=True)
    _thread.start()
    mode = "draft-only" if draft_only else "auto-send"
    where = "Instagram" if bound == "instagram" else (
        "WhatsApp" if bound == "whatsapp" else (win.get("title") or "the open chat")
    )
    return (
        f"Chat automation started on {where} for {who} ({mode}). "
        "I will reply to the latest message now. "
        "Say stop chat automation when you want me to stop."
    )


def stop() -> str:
    save_prefs(chat_active=False)
    _stop.set()
    t = _thread
    if t and t.is_alive():
        t.join(timeout=3.0)
    return "Chat automation stopped."


def send_now(contact: str = "", message: str = "", app: str = "auto", persona: str = "") -> str:
    app = normalize_app(app)
    contact = (contact or "").strip()
    if contact and looks_like_group(contact) and app != "instagram":
        return "I will not auto-chat in a group. Name one person."
    status, win = _prepare(app, contact)
    if not status.startswith("ok"):
        return status
    bound = win.get("app") or app
    hwnd = int(win.get("hwnd") or 0)
    rect = (
        int(win.get("left") or 0),
        int(win.get("top") or 0),
        int(win.get("width") or 0),
        int(win.get("height") or 0),
    )
    reply = (message or "").strip() or draft_reply(
        last_incoming(read_chat(bound, hwnd, rect)) or "Hi",
        persona=persona or str(load_prefs().get("chat_persona") or ""),
        app=bound,
    )
    if not reply:
        return "Could not draft a reply."
    sent = send_reply(reply, bound, hwnd, rect)
    if sent != "ok":
        return sent
    who = contact or str(win.get("title") or bound)
    return f"Sent to {who}: {reply}"


def status_line() -> str:
    p = load_prefs()
    return (
        f"chat_active={bool(p.get('chat_active'))}, "
        f"app={p.get('chat_app') or '(none)'}, "
        f"contact={p.get('chat_contact') or '(none)'}, "
        f"draft_only={bool(p.get('chat_draft_only'))}, "
        f"replies_sent={p.get('chat_replies_sent', 0)}/"
        f"{p.get('chat_max_replies', 20)}, "
        f"persona=\"{p.get('chat_persona')}\"."
    )
