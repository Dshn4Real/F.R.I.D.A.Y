"""WhatsApp Desktop helpers for Friday plugins (Windows-first).

Production rules:
- Never claim success unless we verified the UI actually changed.
- Prefer UI Automation (invoke) over mouse clicks.
- If we must click, click relative to the WhatsApp window — never the whole screen.
- Do not steal the user's clipboard without restoring it.
- Background watchers never auto-click accept/decline unless UIA found the button.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Optional

from core.secure_store import read_json, write_json

pyautogui = None  # filled by _ensure_pya()
pyperclip = None
gw = None

_DPI_AWARE = False
_PYA = False
_CLIP = False
_GW = False
_IMPORT_ERR = ""

_PREFS_LOCK = threading.Lock()
_UI_LOCK = threading.Lock()
_BASE = Path(__file__).resolve().parent.parent
_PREFS_PATH = _BASE / "data" / "whatsapp_prefs.json"

_DEFAULT_PREFS: dict[str, Any] = {
    "calls_armed": False,
    "auto_reply_text": "Can't talk right now - I'll get back to you soon.",
    "default_call_action": "banner",  # banner | decline | accept
    "chat_active": False,
    "chat_contact": "",
    "chat_persona": "polite, brief, natural. Match the user's language.",
    "chat_draft_only": True,
    "chat_max_replies": 20,
    "chat_replies_sent": 0,
    "chat_min_interval_sec": 5,
    "last_sent_reply": "",
}

_VOICE_BTN = (
    "Voice call", "Voice Call", "Audio call", "Phone call", "voice call",
    "Start voice call", "Audio Call",
)
_VIDEO_BTN = (
    "Video call", "Video Call", "video call", "Start video call", "Video Call",
)
_ACCEPT_BTN = ("Accept", "Answer", "Join", "accept")
_DECLINE_BTN = ("Decline", "Reject", "Dismiss", "decline", "Not now")
_IN_CALL_BTN = (
    "End call", "Hang up", "Leave", "Mute", "Unmute", "End Call",
)

_call_stop = threading.Event()
_call_thread: Optional[threading.Thread] = None
_chat_stop = threading.Event()
_chat_thread: Optional[threading.Thread] = None
_on_incoming: Optional[Callable[[dict], None]] = None
_last_call_state = False
_split_cache: tuple[float, int] = (0.0, 0)


def dpi_scale_from(win_dpi: int, sys_dpi: int) -> float:
    """Scale Win32 virtualized coordinates up to physical (mss / SetCursorPos) pixels."""
    sys_dpi = int(sys_dpi or 96) or 96
    win_dpi = int(win_dpi or sys_dpi)
    if sys_dpi <= 96 and win_dpi > 96:
        return float(win_dpi) / 96.0
    return 1.0


def dpi_scale_for_hwnd(hwnd: int = 0) -> float:
    if os.name != "nt":
        return 1.0
    import ctypes

    try:
        user32 = ctypes.windll.user32
        win_dpi = 96
        sys_dpi = int(user32.GetDpiForSystem() or 96) or 96
        if hwnd:
            win_dpi = int(user32.GetDpiForWindow(int(hwnd)) or sys_dpi)
        elif _hwnds_whatsapp():
            win_dpi = int(user32.GetDpiForWindow(int(_hwnds_whatsapp()[0])) or sys_dpi)
        return dpi_scale_from(win_dpi, sys_dpi)
    except Exception:
        return 1.0


def _scale_rect(
    hwnd: int, left: int, top: int, width: int, height: int
) -> tuple[int, int, int, int]:
    # Already-physical rects must not be multiplied again after SetProcessDpiAwareness.
    if _DPI_AWARE:
        return int(left), int(top), int(width), int(height)
    s = dpi_scale_for_hwnd(hwnd)
    if abs(s - 1.0) < 0.02:
        return int(left), int(top), int(width), int(height)
    return (
        int(round(left * s)),
        int(round(top * s)),
        int(round(width * s)),
        int(round(height * s)),
    )


def physical_hwnd_rect(hwnd: int) -> Optional[tuple[int, int, int, int]]:
    """left, top, width, height in physical pixels (mss / SetCursorPos)."""
    if os.name != "nt" or not hwnd:
        return None
    import ctypes
    from ctypes import wintypes

    rc = wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(int(hwnd), ctypes.byref(rc)):
        return None
    w, h = int(rc.right - rc.left), int(rc.bottom - rc.top)
    if w < 80 or h < 80:
        return None
    return _scale_rect(int(hwnd), int(rc.left), int(rc.top), w, h)


def _ensure_dpi_aware() -> None:
    """Physical-pixel clicks must match GetClientRect / ClientToScreen on Win11."""
    global _DPI_AWARE
    if _DPI_AWARE or os.name != "nt":
        return
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    _DPI_AWARE = True


def _ensure_pya() -> bool:
    """Import pyautogui lazily so a broken first import can recover after reinstall."""
    global pyautogui, pyperclip, gw, _PYA, _CLIP, _GW, _IMPORT_ERR
    _ensure_dpi_aware()
    if _PYA:
        return True
    mod = sys.modules.get("pyautogui")
    if mod is not None and not hasattr(mod, "click"):
        for name in list(sys.modules):
            if name == "pyautogui" or name.startswith("pyautogui."):
                sys.modules.pop(name, None)
    try:
        import pyautogui as _p

        _p.FAILSAFE = True
        _p.PAUSE = 0.04
        pyautogui = _p
        _PYA = True
    except Exception as e:
        _IMPORT_ERR = str(e)
        _PYA = False
        return False
    try:
        import pyperclip as _c

        pyperclip = _c
        _CLIP = True
    except Exception:
        _CLIP = False
    try:
        import pygetwindow as _g

        gw = _g
        _GW = True
    except Exception:
        _GW = False
    return True


def _require() -> Optional[str]:
    if _ensure_pya():
        return None
    extra = f" ({_IMPORT_ERR})" if _IMPORT_ERR else ""
    return (
        "PyAutoGUI is not available, so I cannot drive WhatsApp Desktop"
        f"{extra}. Restart Friday after installing: pip install pyautogui pyperclip pygetwindow"
    )


def prefs_path() -> Path:
    return _PREFS_PATH


def load_prefs() -> dict[str, Any]:
    with _PREFS_LOCK:
        data = dict(_DEFAULT_PREFS)
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
        data = dict(_DEFAULT_PREFS)
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


def looks_like_group(name: str) -> bool:
    n = (name or "").strip().lower()
    if not n:
        return False
    if "," in n or "&" in n:
        return True
    return any(w in n for w in ("group", "family gc", "class gc", "the boys", "the girls"))


def _os_name() -> str:
    try:
        from memory.config_manager import load_api_keys
        cfg = load_api_keys()
        return str(cfg.get("os_system") or "windows").lower()
    except Exception:
        return "windows"


def _win_hide() -> dict:
    if os.name != "nt":
        return {}
    return {"creationflags": subprocess.CREATE_NO_WINDOW}


def _failsafe_msg(exc: BaseException) -> Optional[str]:
    name = type(exc).__name__
    text = str(exc).lower()
    if name == "FailSafeException" or "fail-safe" in text or "failsafe" in text:
        return "Stopped — mouse hit a screen corner (safety stop). Move the mouse in and try again."
    return None


@contextmanager
def _ui():
    """Serialize mouse/keyboard so call + chat watchers cannot fight."""
    err = _require()
    if err:
        raise RuntimeError(err)
    if not _UI_LOCK.acquire(timeout=10):
        raise RuntimeError("WhatsApp is busy with another Friday action. Try again in a moment.")
    try:
        yield
    finally:
        _UI_LOCK.release()


def _paste(text: str) -> None:
    osn = _os_name()
    paste = ("command", "v") if osn == "mac" else ("ctrl", "v")
    old = None
    if _CLIP:
        try:
            old = pyperclip.paste()
        except Exception:
            old = None
        pyperclip.copy(text)
        time.sleep(0.08)
        pyautogui.hotkey(*paste)
        time.sleep(0.08)
        if old is not None:
            try:
                pyperclip.copy(old)
            except Exception:
                pass
    else:
        pyautogui.write(text, interval=0.02)
        time.sleep(0.08)


def _whatsapp_pids() -> set[int]:
    try:
        import psutil
    except ImportError:
        return set()
    pids: set[int] = set()
    try:
        for p in psutil.process_iter(["name", "pid"]):
            n = (p.info.get("name") or "").lower()
            if "whatsapp" in n:
                pids.add(int(p.info["pid"]))
    except Exception:
        return pids
    return pids


def _win32_windows(*, visible_only: bool = True) -> list[dict[str, Any]]:
    """Top-level windows via EnumWindows. WinUI Store WhatsApp is often not 'visible' to DPI-aware processes."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    out: list[dict[str, Any]] = []

    @WNDENUMPROC
    def _cb(hwnd, _lp):
        vis = bool(user32.IsWindowVisible(hwnd))
        if visible_only and not vis:
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        title = ""
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            title = buf.value or ""
        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls_buf, 256)
        rc = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rc))
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        out.append(
            {
                "hwnd": int(hwnd),
                "title": title,
                "class": cls_buf.value or "",
                "left": int(rc.left),
                "top": int(rc.top),
                "width": int(rc.right - rc.left),
                "height": int(rc.bottom - rc.top),
                "visible": vis,
                "pid": int(pid.value),
            }
        )
        return True

    user32.EnumWindows(_cb, 0)
    return out


def _hwnds_whatsapp() -> list[int]:
    """Store WhatsApp is WinUI. DPI-aware IsWindowVisible is often False even when it is on screen."""
    pids = _whatsapp_pids()
    wins: list[dict[str, Any]] = []
    if os.name == "nt":
        for w in _win32_windows(visible_only=False):
            title = (w.get("title") or "").strip().lower()
            cls = w.get("class") or ""
            if w["width"] < 200 or w["height"] < 200:
                continue
            owned = int(w.get("pid") or 0) in pids
            named = "whatsapp" in title
            winui = cls == "WinUIDesktopWin32WindowClass" and named
            chrome = cls.startswith("Chrome_WidgetWin") and named
            if owned or named or winui or chrome:
                wins.append(w)
    elif _GW:
        try:
            for w in gw.getAllWindows():
                title = (w.title or "").strip()
                if not title or "whatsapp" not in title.lower():
                    continue
                hwnd = getattr(w, "_hWnd", None)
                if hwnd:
                    wins.append(
                        {
                            "hwnd": int(hwnd),
                            "title": title,
                            "class": "",
                            "left": w.left,
                            "top": w.top,
                            "width": w.width,
                            "height": w.height,
                        }
                    )
        except Exception:
            pass
    wins.sort(key=lambda x: x["width"] * x["height"], reverse=True)
    preferred_cls = ("WinUIDesktopWin32WindowClass", "Chrome_WidgetWin_1")
    preferred = [w for w in wins if w.get("class") in preferred_cls]
    ordered = preferred + [w for w in wins if w not in preferred]
    seen: list[int] = []
    for w in ordered:
        h = int(w["hwnd"])
        if h not in seen:
            seen.append(h)
    return seen


def _force_foreground(hwnd: int) -> bool:
    if os.name != "nt" or not hwnd:
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    SW_RESTORE = 9
    SW_SHOWMAXIMIZED = 3
    SW_SHOW = 5
    WM_SYSCOMMAND = 0x0112
    SC_MAXIMIZE = 0xF030

    # SW_RESTORE un-maximizes a fullscreen window. Only use it to un-minimize.
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.12)
    if not user32.IsWindowVisible(hwnd):
        user32.ShowWindow(hwnd, SW_SHOW)
        time.sleep(0.08)
    user32.ShowWindow(hwnd, SW_SHOWMAXIMIZED)
    user32.PostMessageW(hwnd, WM_SYSCOMMAND, SC_MAXIMIZE, 0)

    fg = user32.GetForegroundWindow()
    cur = kernel32.GetCurrentThreadId()
    pid = wintypes.DWORD()
    fg_tid = user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
    user32.AttachThreadInput(cur, fg_tid, True)
    # Alt key lets a background process steal focus on modern Windows.
    user32.keybd_event(0x12, 0, 0, 0)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(0x12, 0, 2, 0)
    HWND_TOPMOST = -1
    HWND_NOTOPMOST = -2
    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001
    SWP_SHOWWINDOW = 0x0040
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
    user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
    user32.AttachThreadInput(cur, fg_tid, False)
    time.sleep(0.25)
    # Focus-stealing rules often deny SetForegroundWindow; TOPMOST pulse
    # still raises the window so clicks land on WhatsApp, not the IDE.
    return True


@contextmanager
def whatsapp_front():
    """Keep WhatsApp above FRIDAY's always-on-top HUD while we screenshot/type."""
    hwnds = _hwnds_whatsapp() if os.name == "nt" else []
    if not hwnds:
        yield
        return
    import ctypes

    user32 = ctypes.windll.user32
    HWND_TOPMOST = -1
    HWND_NOTOPMOST = -2
    flags = 0x0002 | 0x0001 | 0x0040  # NOSIZE | NOMOVE | SHOWWINDOW
    try:
        for hwnd in hwnds:
            user32.SetWindowPos(int(hwnd), HWND_TOPMOST, 0, 0, 0, 0, flags)
        _force_foreground(int(hwnds[0]))
        for hwnd in hwnds:
            user32.SetWindowPos(int(hwnd), HWND_TOPMOST, 0, 0, 0, 0, flags)
        time.sleep(0.12)
        yield
    finally:
        for hwnd in hwnds:
            try:
                user32.SetWindowPos(int(hwnd), HWND_NOTOPMOST, 0, 0, 0, 0, flags)
            except Exception:
                pass


def _maximize_whatsapp() -> None:
    """Keep WhatsApp maximized while we drive it — never restore-to-windowed."""
    hwnds = _hwnds_whatsapp()
    if hwnds:
        _force_foreground(hwnds[0])
        return
    if not _GW:
        return
    try:
        for w in gw.getAllWindows():
            title = (w.title or "").strip()
            if not title or "whatsapp" not in title.lower():
                continue
            try:
                if getattr(w, "isMinimized", False):
                    w.restore()
                    time.sleep(0.1)
                w.maximize()
            except Exception:
                pass
    except Exception:
        pass


def _whatsapp_rect() -> Optional[tuple[int, int, int, int]]:
    """left, top, width, height of the largest WhatsApp window (physical pixels)."""
    hwnds = _hwnds_whatsapp()
    if os.name == "nt" and hwnds:
        phys = physical_hwnd_rect(int(hwnds[0]))
        if phys:
            return phys
        for w in _win32_windows(visible_only=False):
            if w["hwnd"] == hwnds[0] and w["width"] >= 200 and w["height"] >= 200:
                return _scale_rect(
                    int(hwnds[0]), w["left"], w["top"], w["width"], w["height"]
                )
    if not _GW:
        return None
    best = None
    try:
        for w in gw.getAllWindows():
            title = (w.title or "").strip()
            if not title or "whatsapp" not in title.lower():
                continue
            if w.width < 200 or w.height < 200:
                continue
            area = w.width * w.height
            if best is None or area > best[0]:
                best = (area, w.left, w.top, w.width, w.height)
    except Exception:
        return None
    if not best:
        return None
    return best[1], best[2], best[3], best[4]


def _focus_whatsapp_window() -> bool:
    hwnds = _hwnds_whatsapp()
    for hwnd in hwnds:
        if _force_foreground(hwnd):
            return True
    if hwnds:
        _maximize_whatsapp()
        return True
    if os.name == "nt":
        try:
            subprocess.run(
                [
                    "powershell", "-NoProfile", "-NonInteractive", "-Command",
                    "(New-Object -ComObject WScript.Shell).AppActivate('WhatsApp')",
                ],
                capture_output=True,
                timeout=5,
                **_win_hide(),
            )
            time.sleep(0.2)
            _maximize_whatsapp()
            return bool(_hwnds_whatsapp())
        except Exception:
            return False
    return False


def _launch_whatsapp_windows() -> None:
    os.environ.setdefault("PYTHONUTF8", "1")
    try:
        os.startfile("whatsapp:")  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        subprocess.Popen(
            [
                "explorer.exe",
                "shell:AppsFolder\\5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "WhatsApp" / "WhatsApp.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "WhatsApp" / "WhatsApp.exe",
    ]
    for path in candidates:
        if path.is_file():
            subprocess.Popen([str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    subprocess.Popen(
        ["cmd", "/c", "start", "", "whatsapp:"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **_win_hide(),
    )


def _whatsapp_is_on_screen() -> bool:
    """True if WhatsApp is running and not minimized.

    Store/WinUI WhatsApp often reports IsWindowVisible=False even when it is
    on screen, so we must not treat that flag as 'closed'.
    """
    hwnds = _hwnds_whatsapp()
    if not hwnds:
        return False
    if os.name != "nt":
        return True
    import ctypes

    user32 = ctypes.windll.user32
    for hwnd in hwnds:
        if not user32.IsIconic(hwnd):
            return True
    return False


def ensure_whatsapp_open(*, wait: float = 8.0) -> str:
    err = _require()
    if err:
        return err
    if _whatsapp_is_on_screen():
        _maximize_whatsapp()
        _focus_whatsapp_window()
        return "ok"
    osn = _os_name()
    try:
        if osn == "windows":
            _launch_whatsapp_windows()
        elif osn == "mac":
            subprocess.run(["open", "-a", "WhatsApp"], timeout=10)
        else:
            subprocess.Popen(
                ["whatsapp-desktop"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception as e:
        return f"Could not launch WhatsApp: {e}"

    deadline = time.time() + max(wait, 2.0)
    while time.time() < deadline:
        time.sleep(0.4)
        if _whatsapp_is_on_screen():
            _focus_whatsapp_window()
            _maximize_whatsapp()
            return "ok"
    return (
        "WhatsApp Desktop did not open. Install WhatsApp from the Microsoft Store "
        "or whatsapp.com, log in, then try again."
    )


def _whatsapp_client_rect() -> Optional[tuple[int, int, int, int]]:
    """Client area in screen pixels (ignores Win11 drop-shadow / -8 maximized offsets)."""
    hwnds = _hwnds_whatsapp()
    if not hwnds or os.name != "nt":
        rect = _whatsapp_rect()
        return rect
    import ctypes
    from ctypes import wintypes

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    user32 = ctypes.windll.user32
    rc = wintypes.RECT()
    hwnd = hwnds[0]
    if not user32.GetClientRect(hwnd, ctypes.byref(rc)):
        return _whatsapp_rect()
    pt = POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    w, h = int(rc.right - rc.left), int(rc.bottom - rc.top)
    if w < 200 or h < 200:
        return _whatsapp_rect()
    return _scale_rect(int(hwnd), int(pt.x), int(pt.y), w, h)


def _click_screen(x: int, y: int, *, clicks: int = 1) -> None:
    """Physical-pixel click. Move first so WebView2 sees hover, then down/up."""
    x, y = int(x), int(y)
    if os.name == "nt":
        import ctypes

        user32 = ctypes.windll.user32
        user32.SetCursorPos(x - 6, y - 6)
        time.sleep(0.03)
        user32.SetCursorPos(x, y)
        time.sleep(0.08)
        for _ in range(max(1, clicks)):
            user32.mouse_event(0x0002, 0, 0, 0, 0)
            time.sleep(0.04)
            user32.mouse_event(0x0004, 0, 0, 0, 0)
            time.sleep(0.07)
        return
    pyautogui.click(x, y, clicks=clicks)


# WhatsApp Desktop: left icon rail is ~72 CSS px, chat list ~365 CSS px.
# These are scaled to physical pixels. Prefer the live splitter when UIA sees it.
_NAV_RAIL = 72
_LIST_WIDTH = 365
_TITLEBAR = 40


def _list_pane_rect() -> Optional[tuple[int, int, int, int]]:
    """Chat/search list (not the icon rail, not the conversation)."""
    rect = _whatsapp_client_rect()
    if not rect:
        return None
    left, top, width, height = rect
    split = _conversation_split_x(left, width)
    hwnds = _hwnds_whatsapp()
    s = dpi_scale_for_hwnd(int(hwnds[0])) if hwnds else 1.0
    rail = max(48, int(round(_NAV_RAIL * s)))
    list_left = left + rail
    list_w = max(200, split - list_left)
    list_w = min(list_w, max(200, width - rail - 240))
    return list_left, top, list_w, height


def _conversation_split_x(client_left: int, client_width: int) -> int:
    """Physical X where the chat list ends and the thread begins."""
    global _split_cache
    now = time.time()
    cached_at, cached_x = _split_cache
    if cached_x and now - cached_at < 20:
        return cached_x
    x = 0
    try:
        win = _whatsapp_uia_window()
        if win is not None:
            btn = win.child_window(title="Resize the chat list panel", control_type="Button")
            if btn.exists(timeout=0.35):
                x = int(btn.rectangle().left)
    except Exception:
        x = 0
    if x < client_left + 160:
        x = client_left + int(client_width * 0.29)
    _split_cache = (now, x)
    return x


def _chat_pane_rect() -> Optional[tuple[int, int, int, int]]:
    """Conversation pane — call icons live in its header."""
    rect = _whatsapp_client_rect()
    if not rect:
        return None
    left, top, width, height = rect
    split = _conversation_split_x(left, width)
    conv_left = min(max(split + 6, left + 180), left + width - 200)
    return conv_left, top, max(200, left + width - conv_left), height


def _whatsapp_uia_window():
    """UIA wrapper for the WhatsApp window, including WinUI 'invisible' hwnds."""
    hwnds = _hwnds_whatsapp()
    if not hwnds:
        return None
    try:
        from pywinauto import Application
    except Exception:
        return None
    for hwnd in reversed(list(hwnds)):
        try:
            app = Application(backend="uia").connect(handle=int(hwnd), timeout=2)
            return app.window(handle=int(hwnd))
        except Exception:
            continue
    return None


def conversation_is_empty() -> bool:
    """True when WhatsApp is on the home pane (Ask Meta AI), not a 1:1 thread."""
    try:
        win = _whatsapp_uia_window()
        if win is None:
            return False
        btn = win.child_window(title="Ask Meta AI", control_type="Button")
        return bool(btn.exists(timeout=0.3))
    except Exception:
        return False


def _grab_region(x: int, y: int, w: int, h: int):
    if w < 8 or h < 8:
        return None
    try:
        import mss
        import numpy as np

        with mss.mss() as sct:
            shot = sct.grab({"left": int(x), "top": int(y), "width": int(w), "height": int(h)})
            return np.array(shot)
    except Exception:
        try:
            from PIL import ImageGrab
            import numpy as np

            im = ImageGrab.grab(bbox=(int(x), int(y), int(x) + int(w), int(y) + int(h)))
            return np.array(im)
        except Exception:
            return None


def _visual_header_icons() -> list[tuple[int, int]]:
    """Screen centers of header action icons (search / video / voice / menu)."""
    pane = _chat_pane_rect()
    if not pane:
        return []
    left, top, width, height = pane
    hx = left + max(width - 300, 8)
    hy = top + _TITLEBAR + 4
    hw, hh = min(290, max(80, width - 24)), 64
    img = _grab_region(hx, hy, hw, hh)
    if img is None:
        return []
    try:
        import cv2
        import numpy as np
    except Exception:
        return []
    arr = np.array(img)
    if arr.ndim == 3 and arr.shape[2] == 4:
        arr = arr[:, :, :3]
    gray = arr.mean(axis=2)
    med = float(np.median(gray))
    mask = (np.abs(gray - med) > 18).astype("uint8") * 255
    n, _labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    pts: list[tuple[int, int, int]] = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if not (14 <= bw <= 64 and 14 <= bh <= 64 and area >= 40):
            continue
        pts.append((hx + int(centroids[i][0]), hy + int(centroids[i][1]), int(area)))
    pts.sort(key=lambda p: p[0])
    uniq: list[tuple[int, int]] = []
    for x, y, _a in pts:
        if any(abs(x - u[0]) < 14 and abs(y - u[1]) < 14 for u in uniq):
            continue
        uniq.append((x, y))
    return uniq


def _compose_bar_visible() -> bool:
    pane = _chat_pane_rect()
    if not pane:
        return False
    left, top, width, height = pane
    bar = _grab_region(
        left + int(width * 0.16),
        top + height - 78,
        max(80, int(width * 0.68)),
        56,
    )
    if bar is None:
        return False
    if bar.ndim == 3 and bar.shape[2] == 4:
        bar = bar[:, :, :3]
    return float(bar.std()) > 11


def _main_has_button(*names: str, timeout: float = 0.35) -> bool:
    """Named button on the main WhatsApp window. Does not walk the WebView tree."""
    win = _whatsapp_uia_window()
    if win is None:
        return False
    for name in names:
        if not name:
            continue
        try:
            btn = win.child_window(title=name, control_type="Button")
            if btn.exists(timeout=timeout):
                return True
        except Exception:
            continue
    return False


def _invoke_main_button(names: tuple[str, ...], *, timeout: float = 0.55) -> bool:
    """Invoke a header/chrome button on the main window (Voice call, Video call, …)."""
    win = _whatsapp_uia_window()
    if win is None:
        return False
    seen: set[str] = set()
    first = True
    for name in names:
        key = (name or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        wait = timeout if first else 0.12
        first = False
        try:
            btn = win.child_window(title=name, control_type="Button")
            if not btn.exists(timeout=wait):
                continue
            if _invoke_named(btn):
                print(f"[WhatsApp] invoked main button {name!r}")
                return True
        except Exception:
            continue
    return False


def _conversation_looks_open() -> bool:
    """True when a 1:1 thread is showing — Voice call lives in that header."""
    if _main_has_button("Voice call", "Video call", timeout=0.35):
        print("[WhatsApp] conversation open via Voice/Video call button")
        return True
    if _main_has_button("Ask Meta AI", timeout=0.2):
        return False
    icons = _visual_header_icons()
    if len(icons) >= 3:
        print(f"[WhatsApp] conversation open via {len(icons)} header icons")
        return True
    if _compose_bar_visible():
        print("[WhatsApp] conversation open via compose bar")
        return True
    return False


def _click_compose_box() -> None:
    """Focus the message box — only after a chat is actually open."""
    if not _conversation_looks_open():
        return
    rect = _chat_pane_rect() or _whatsapp_client_rect()
    if not rect:
        return
    left, top, width, height = rect
    _click_screen(left + int(width * 0.50), top + height - 52)
    time.sleep(0.15)


def _focus_conversation_body() -> None:
    rect = _chat_pane_rect() or _whatsapp_client_rect()
    if not rect:
        return
    left, top, width, height = rect
    _click_screen(left + int(width * 0.50), top + int(height * 0.55))
    time.sleep(0.12)


def _cdp_listening_port() -> Optional[int]:
    import socket

    for port in (9222, 9223, 9232):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.12):
                return port
        except OSError:
            continue
    return None


def _uia_windows(*, popups_only: bool = False):
    """WhatsApp windows via UI Automation.

    Store/WinUI WhatsApp often titles the window with the *contact name*, not
    'WhatsApp'. Matching only on title therefore missed the main chat window.
    We also match windows owned by a WhatsApp process.
    """
    from pywinauto import Desktop

    desk = Desktop(backend="uia")
    pids = _whatsapp_pids()
    found = []
    for w in desk.windows():
        try:
            title = (w.window_text() or "").lower()
        except Exception:
            continue
        try:
            pid = int(w.element_info.process_id)
        except Exception:
            pid = 0
        named = any(k in title for k in ("whatsapp", "incoming", "voice call", "video call"))
        owned = bool(pids) and pid in pids
        if not (named or owned):
            continue
        if popups_only:
            try:
                r = w.rectangle()
                if (r.bottom - r.top) > 520 and "incoming" not in title:
                    continue
            except Exception:
                continue
        found.append(w)
    return found


def _invoke_named(ctrl) -> bool:
    try:
        ctrl.invoke()
        return True
    except Exception:
        try:
            ctrl.click_input()
            return True
        except Exception:
            try:
                r = ctrl.rectangle()
                _click_screen((r.left + r.right) // 2, (r.top + r.bottom) // 2)
                return True
            except Exception:
                return False


def _invoke_button(names: tuple[str, ...], *, timeout: float = 0.55) -> bool:
    pattern = "|".join(re.escape(n) for n in names if n)
    if not pattern:
        return False
    title_re = f"(?i).*({pattern}).*"
    try:
        windows = _uia_windows(popups_only=True)
    except Exception:
        return False
    for w in windows:
        try:
            btn = w.child_window(title_re=title_re, control_type="Button")
            if not btn.exists(timeout=timeout):
                continue
            if _invoke_named(btn):
                return True
        except Exception:
            continue
    return False


def _button_exists(names: tuple[str, ...], *, timeout: float = 0.35) -> bool:
    pattern = "|".join(re.escape(n) for n in names if n)
    if not pattern:
        return False
    title_re = f"(?i).*({pattern}).*"
    try:
        for w in _uia_windows(popups_only=True):
            try:
                btn = w.child_window(title_re=title_re, control_type="Button")
                if btn.exists(timeout=timeout):
                    return True
            except Exception:
                continue
    except Exception:
        return False
    return False


def _with_whatsapp_cdp_page():
    """Return Playwright page for web.whatsapp.com when CDP debugging is enabled."""
    port = _cdp_listening_port()
    if not port:
        return None, None
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, None
    try:
        pw = sync_playwright().start()
    except Exception:
        return None, None
    try:
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        for ctx in browser.contexts:
            for page in ctx.pages:
                if "web.whatsapp.com" in (page.url or ""):
                    return pw, page
    except Exception:
        pass
    try:
        pw.stop()
    except Exception:
        pass
    return None, None


def _compose_visible() -> bool:
    return _conversation_looks_open()


def _search_still_open() -> bool:
    return not _conversation_looks_open()


def _chat_is_open(contact: str) -> bool:
    return _conversation_looks_open()


_OPEN_CHAT_JS = """
(contact) => {
  const needle = (contact || '').trim().toLowerCase();
  if (!needle) return false;
  const rows = [
    ...document.querySelectorAll('[data-testid="cell-frame-container"]'),
    ...document.querySelectorAll('[role="listitem"]'),
  ];
  let best = null;
  let bestScore = -1;
  for (const row of rows) {
    const titleEl = row.querySelector('span[title], span[dir="auto"]');
    const title = (titleEl?.getAttribute('title') || titleEl?.textContent || '').trim().toLowerCase();
    if (!title) continue;
    let score = 0;
    if (title === needle) score = 100;
    else if (title.startsWith(needle)) score = 80;
    else if (title.includes(needle)) score = 50;
    else continue;
    if (score > bestScore) { bestScore = score; best = row; }
  }
  if (!best) return false;
  const target = best.querySelector('[tabindex="-1"]') || best;
  const r = target.getBoundingClientRect();
  const x = r.left + r.width / 2;
  const y = r.top + r.height / 2;
  const opts = {
    bubbles: true, cancelable: true, composed: true, view: window,
    clientX: x, clientY: y, button: 0, buttons: 1,
  };
  target.dispatchEvent(new PointerEvent('pointerdown', opts));
  target.dispatchEvent(new MouseEvent('mousedown', opts));
  target.dispatchEvent(new PointerEvent('pointerup', opts));
  target.dispatchEvent(new MouseEvent('mouseup', opts));
  target.dispatchEvent(new MouseEvent('click', opts));
  return true;
}
"""


def _open_chat_via_cdp(contact: str) -> bool:
    pw, page = _with_whatsapp_cdp_page()
    if not page:
        return False
    try:
        ok = page.evaluate(_OPEN_CHAT_JS, contact)
        if ok:
            time.sleep(0.55)
        return bool(ok) and _conversation_looks_open()
    except Exception:
        return False
    finally:
        try:
            pw.stop()
        except Exception:
            pass


def contact_list_item_re(contact: str) -> str:
    """Match a chat-list row whose first line is the contact name."""
    needle = (contact or "").strip()
    return f"(?i)^{re.escape(needle)}(\\n|$).*"


def _uia_open_chat(contact: str) -> bool:
    """Open a chat row by name on the main window — no full-tree walk."""
    needle = (contact or "").strip()
    if not needle:
        return False
    win = _whatsapp_uia_window()
    if win is None:
        return False
    try:
        item = win.child_window(
            title_re=contact_list_item_re(needle),
            control_type="ListItem",
        )
        if item.exists(timeout=0.7) and _invoke_named(item):
            time.sleep(0.45)
            return _conversation_looks_open()
    except Exception:
        pass
    try:
        item = win.child_window(title=needle, control_type="ListItem")
        if item.exists(timeout=0.4) and _invoke_named(item):
            time.sleep(0.45)
            return _conversation_looks_open()
    except Exception:
        pass
    return False


def search_edit_kind(name: str) -> str:
    """'all', 'favourites', or '' for a WhatsApp search field label."""
    n = (name or "").strip().lower()
    if not n or "search" not in n:
        return ""
    if "favourite" in n or "favorite" in n:
        return "favourites"
    return "all"


def _select_chats_filter_all() -> bool:
    """Leave the Favourites tab so search covers every chat."""
    win = _whatsapp_uia_window()
    if win is None:
        return False
    try:
        tab = win.child_window(title="All", control_type="TabItem")
        if not tab.exists(timeout=0.45):
            return False
        try:
            if tab.is_selected():
                return True
        except Exception:
            pass
        tab.select()
        time.sleep(0.3)
        try:
            return bool(tab.is_selected())
        except Exception:
            return True
    except Exception:
        return False


def _list_search_edit(*, allow_favourites: bool = False):
    """Left-pane all-chats search. Never the header Search button or compose box."""
    win = _whatsapp_uia_window()
    if win is None:
        return None
    try:
        edits = win.descendants(control_type="Edit")
    except Exception:
        return None
    pane = _list_pane_rect()
    favoured = None
    generic = None
    for ed in edits:
        try:
            r = ed.rectangle()
            name = (ed.window_text() or "").strip()
        except Exception:
            continue
        if (r.bottom - r.top) > 90 or r.top > 420:
            continue
        in_list = True
        if pane:
            pl, _pt, pw, _ph = pane
            in_list = r.left >= pl - 24 and r.right <= pl + pw + 24
        kind = search_edit_kind(name)
        if kind == "all":
            low = name.lower()
            if any(k in low for k in ("start a new", "username", "number")):
                return ed
            generic = ed
            continue
        if kind == "favourites":
            favoured = ed
            continue
        if in_list and generic is None:
            generic = ed
    if generic is not None:
        return generic
    if allow_favourites:
        return favoured
    return None


def _click_search_edit(ed) -> None:
    try:
        r = ed.rectangle()
        _click_screen((r.left + r.right) // 2, (r.top + r.bottom) // 2)
    except Exception:
        pass
    try:
        ed.set_focus()
    except Exception:
        pass
    time.sleep(0.15)


def _focus_search_box() -> None:
    """Focus all-chats search — never the Favourites-only field."""
    ed = _list_search_edit()
    if ed is not None:
        _click_search_edit(ed)
        return
    _select_chats_filter_all()
    time.sleep(0.15)
    ed = _list_search_edit()
    if ed is not None:
        _click_search_edit(ed)
        return
    if _invoke_main_button(("New chat",)):
        time.sleep(0.45)
        ed = _list_search_edit()
        if ed is not None:
            _click_search_edit(ed)
            return
    print("[WhatsApp] all-chats search field not found")


def _click_first_search_result(contact: str) -> bool:
    """Open the matching row, or the first row under the search field."""
    if _uia_open_chat(contact):
        return True
    ed = _list_search_edit()
    if ed is None:
        return False
    try:
        r = ed.rectangle()
    except Exception:
        return False
    x = (r.left + r.right) // 2
    for dy in (48, 72, 96):
        y = r.bottom + dy
        print(f"[WhatsApp] click result '{contact}' at ({x},{y})")
        _click_screen(x, y)
        time.sleep(0.4)
        if _conversation_looks_open():
            return True
    return False


def _select_search_result(contact: str) -> bool:
    """Open this contact's row. Do not treat 'some chat is already open' as success."""
    if _cdp_listening_port() and _open_chat_via_cdp(contact):
        return True
    if _uia_open_chat(contact):
        return True

    pyautogui.press("down")
    time.sleep(0.1)
    pyautogui.press("enter")
    time.sleep(0.4)
    if _uia_open_chat(contact):
        return True

    return _click_first_search_result(contact)


def _open_chat_unlocked(contact: str) -> str:
    opened = ensure_whatsapp_open()
    if opened != "ok":
        return opened
    contact = (contact or "").strip()
    if not _whatsapp_is_on_screen():
        _launch_whatsapp_windows()
        time.sleep(1.2)
    _maximize_whatsapp()
    _focus_whatsapp_window()
    time.sleep(0.25)

    _focus_search_box()
    osn = _os_name()
    select = ("command", "a") if osn == "mac" else ("ctrl", "a")
    pyautogui.hotkey(*select)
    time.sleep(0.05)
    pyautogui.press("backspace")
    time.sleep(0.06)
    _paste(contact)
    time.sleep(0.9)

    if not _select_search_result(contact):
        return (
            f"Found {contact} in WhatsApp search but could not open the chat. "
            "Click their name in the search results, then ask me to call again."
        )

    time.sleep(0.2)
    return f"ok:{contact}"


def open_chat(contact: str) -> str:
    err = _require()
    if err:
        return err
    contact = (contact or "").strip()
    if not contact:
        return "Please specify a contact name."
    try:
        with _ui():
            with whatsapp_front():
                return _open_chat_unlocked(contact)
    except Exception as e:
        return _failsafe_msg(e) or f"Could not open the WhatsApp chat: {e}"


def send_chat_text(text: str) -> str:
    err = _require()
    if err:
        return err
    text = (text or "").strip()
    if not text:
        return "Empty message."
    try:
        with _ui():
            with whatsapp_front():
                if not _focus_whatsapp_window():
                    opened = ensure_whatsapp_open()
                    if opened != "ok":
                        return opened
                time.sleep(0.12)
                # Search stays focused after open_chat. Paste+Enter there
                # searches a name instead of sending a message.
                _click_compose_box()
                rect = _chat_pane_rect() or _whatsapp_client_rect()
                if rect:
                    left, top, width, height = rect
                    _click_screen(left + int(width * 0.50), top + max(40, height - 56))
                    time.sleep(0.12)
                _paste(text)
                time.sleep(0.12)
                pyautogui.press("enter")
                time.sleep(0.2)
                return "ok"
    except Exception as e:
        return _failsafe_msg(e) or f"Could not send the WhatsApp message: {e}"


def open_chat_and_send(contact: str, text: str) -> str:
    r = open_chat(contact)
    if not r.startswith("ok"):
        return r
    s = send_chat_text(text)
    if s != "ok":
        return s
    return f"Message sent to {contact} on WhatsApp."


def _window_snapshot() -> tuple:
    try:
        rows = []
        for w in _win32_windows(visible_only=False) if os.name == "nt" else []:
            t = (w.get("title") or "").strip()
            if not t:
                continue
            rows.append((t, w["left"], w["top"], w["width"], w["height"]))
        if rows:
            return tuple(sorted(rows))
        if not _GW:
            return tuple()
        for w in gw.getAllWindows():
            t = (w.title or "").strip()
            if not t:
                continue
            rows.append((t, w.left, w.top, w.width, w.height))
        return tuple(sorted(rows))
    except Exception:
        return tuple()


_CALL_TITLE_KEYS = (
    "calling",
    "ringing",
    "voice call",
    "video call",
    "whatsapp call",
    "in call",
    "is calling",
)


def _center_shot():
    pane = _chat_pane_rect() or _whatsapp_client_rect()
    if not pane:
        return None
    left, top, width, height = pane
    return _grab_region(
        left + max(20, width // 2 - 120),
        top + max(80, height // 2 - 120),
        240,
        240,
    )


def _has_end_call_blob() -> bool:
    rect = _whatsapp_client_rect() or _chat_pane_rect()
    if not rect:
        return False
    left, top, width, height = rect
    img = _grab_region(
        left + int(width * 0.22),
        top + int(height * 0.45),
        max(80, int(width * 0.56)),
        max(80, int(height * 0.45)),
    )
    if img is None:
        return False
    if img.ndim == 3 and img.shape[2] >= 3:
        c0, c1, c2 = img[:, :, 0], img[:, :, 1], img[:, :, 2]
        red_rgb = (c0 > 140) & (c1 < 95) & (c2 < 95)
        red_bgr = (c2 > 140) & (c1 < 95) & (c0 < 95)
        return float(red_rgb.mean()) > 0.0015 or float(red_bgr.mean()) > 0.0015
    return False


def _call_started(before: tuple, before_center=None) -> bool:
    if _main_has_button("End call", "Mute", timeout=0.25):
        return True
    try:
        for w in _win32_windows(visible_only=False) if os.name == "nt" else []:
            t = (w.get("title") or "").lower()
            if any(k in t for k in _CALL_TITLE_KEYS):
                return True
    except Exception:
        pass
    if _has_end_call_blob():
        return True
    after_center = _center_shot()
    if before_center is not None and after_center is not None:
        try:
            import numpy as np

            a = np.array(before_center, dtype=float)
            b = np.array(after_center, dtype=float)
            if a.shape == b.shape and float(np.abs(a - b).mean()) > 18:
                return True
        except Exception:
            pass
    after = _window_snapshot()
    if before and after and after != before:
        new_titles = {r[0].lower() for r in after} - {r[0].lower() for r in before}
        if any("whatsapp" in t or "call" in t for t in new_titles):
            return True
        before_n = sum(1 for r in before if "whatsapp" in r[0].lower())
        after_n = sum(1 for r in after if "whatsapp" in r[0].lower())
        if after_n > before_n:
            return True
    return False


def _header_icon_buttons(deadline: float) -> list:
    """Square-ish controls in the chat header (top-right). Right-to-left order."""
    found = []
    try:
        windows = _uia_windows()
    except Exception:
        return found
    ctrl_types = ("Button", "Group", "Image", "Hyperlink", "Custom")
    for w in windows:
        if time.time() > deadline:
            break
        try:
            wr = w.rectangle()
        except Exception:
            continue
        if (wr.bottom - wr.top) > 600:
            continue
        header_bottom = wr.top + 130
        header_left = wr.left + int((wr.right - wr.left) * 0.28)
        for ctype in ctrl_types:
            if time.time() > deadline:
                break
            try:
                controls = w.descendants(control_type=ctype)
            except Exception:
                continue
            for btn in controls:
                if time.time() > deadline:
                    break
                try:
                    r = btn.rectangle()
                except Exception:
                    continue
                bw, bh = r.right - r.left, r.bottom - r.top
                if bw < 14 or bh < 14 or bw > 88 or bh > 88:
                    continue
                if r.top < wr.top - 6 or r.bottom > header_bottom:
                    continue
                if r.left < header_left:
                    continue
                name = ""
                helptext = ""
                auto_id = ""
                try:
                    name = (btn.window_text() or "").strip()
                except Exception:
                    pass
                try:
                    auto_id = (btn.element_info.automation_id or "").strip()
                except Exception:
                    pass
                try:
                    lp = btn.legacy_properties() or {}
                    helptext = str(lp.get("Help") or lp.get("Description") or "")
                except Exception:
                    pass
                cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
                found.append(
                    {
                        "ctrl": btn,
                        "x": cx,
                        "y": cy,
                        "name": f"{name} {helptext} {auto_id}".lower(),
                    }
                )
    found.sort(key=lambda b: b["x"])
    uniq = []
    for b in found:
        if any(abs(b["x"] - u["x"]) < 12 and abs(b["y"] - u["y"]) < 12 for u in uniq):
            continue
        uniq.append(b)
    return uniq


_FORCE_CLICK_JS = """
(sel) => {
  const el = document.querySelector(sel);
  if (!el) return false;
  const target = el.closest('button,[role="button"]') || el;
  const r = target.getBoundingClientRect();
  if (!r.width || !r.height) return false;
  const x = r.left + r.width / 2;
  const y = r.top + r.height / 2;
  const opts = {
    bubbles: true, cancelable: true, composed: true, view: window,
    clientX: x, clientY: y, button: 0, buttons: 1,
  };
  target.dispatchEvent(new PointerEvent('pointerdown', opts));
  target.dispatchEvent(new MouseEvent('mousedown', opts));
  target.dispatchEvent(new PointerEvent('pointerup', opts));
  target.dispatchEvent(new MouseEvent('mouseup', opts));
  target.dispatchEvent(new MouseEvent('click', opts));
  return true;
}
"""


def _click_call_via_cdp(*, video: bool) -> bool:
    """Click call button in WhatsApp WebView2 when remote debugging is enabled."""
    pw, page = _with_whatsapp_cdp_page()
    if not page:
        return False
    selectors = (
        [
            'button[aria-label="Video call"]',
            'span[data-icon="video-call"]',
            '[data-icon="video-call"]',
            '[aria-label*="ideo call" i]',
        ]
        if video
        else [
            'button[aria-label="Voice call"]',
            'span[data-icon="audio-call"]',
            'span[data-icon="voice-call"]',
            '[data-icon="audio-call"]',
            '[aria-label*="oice call" i]',
        ]
    )
    try:
        for sel in selectors:
            try:
                if page.evaluate(_FORCE_CLICK_JS, sel):
                    time.sleep(0.45)
                    return True
            except Exception:
                continue
        return False
    finally:
        try:
            pw.stop()
        except Exception:
            pass


def _scan_header_clicks(*, video: bool, before: tuple, before_center=None) -> bool:
    """Last-resort click on the Voice/Video UIA rect, then one nearby slot."""
    win = _whatsapp_uia_window()
    name = "Video call" if video else "Voice call"
    if win is not None:
        try:
            btn = win.child_window(title=name, control_type="Button")
            if btn.exists(timeout=0.5):
                r = btn.rectangle()
                x, y = (r.left + r.right) // 2, (r.top + r.bottom) // 2
                print(f"[WhatsApp] click {name} rect at ({x},{y})")
                _click_screen(x, y)
                time.sleep(0.7)
                if _call_started(before, before_center):
                    return True
        except Exception:
            pass
    pane = _chat_pane_rect()
    if not pane:
        return False
    pl, pt, pw, _ph = pane
    x_off = 215 if video else 145
    y = pt + 88
    x = pl + pw - x_off
    print(f"[WhatsApp] click call icon at ({x},{y}) video={video}")
    _click_screen(x, y)
    time.sleep(0.7)
    return _call_started(before, before_center)


def _click_visual_call_icons(*, video: bool, before: tuple, before_center=None) -> bool:
    icons = _visual_header_icons()
    print(f"[WhatsApp] visual header icons={icons}")
    if len(icons) < 2:
        return False
    # L→R is usually search, video, voice, menu — or voice, video, search, menu.
    if video:
        picks = []
        if len(icons) >= 4:
            picks = [icons[-3], icons[1]]
        elif len(icons) >= 3:
            picks = [icons[-3], icons[1], icons[-2]]
        else:
            picks = [icons[0]]
    else:
        picks = []
        if len(icons) >= 4:
            picks = [icons[-2], icons[0], icons[-4]]
        elif len(icons) >= 3:
            picks = [icons[-2], icons[0]]
        else:
            picks = [icons[0]]
    seen = set()
    for x, y in picks:
        key = (x, y)
        if key in seen:
            continue
        seen.add(key)
        print(f"[WhatsApp] click visual call icon at ({x},{y}) video={video}")
        _click_screen(x, y)
        time.sleep(0.75)
        if _call_started(before, before_center):
            return True
    return False


def _click_header_call(*, video: bool = False, contact: str = "") -> bool:
    before = _window_snapshot()
    before_center = _center_shot()
    _maximize_whatsapp()
    _focus_whatsapp_window()
    time.sleep(0.15)
    if not _conversation_looks_open():
        if contact:
            _select_search_result(contact)
        if not _conversation_looks_open():
            return False

    names = _VIDEO_BTN if video else _VOICE_BTN
    if _invoke_main_button(names):
        time.sleep(0.45)
        if _call_started(before, before_center):
            return True
        time.sleep(0.7)
        if _call_started(before, before_center):
            return True

    if _cdp_listening_port() and _click_call_via_cdp(video=video) and _call_started(before, before_center):
        return True

    if _scan_header_clicks(video=video, before=before, before_center=before_center):
        return True
    return False


def _call_ui_visible() -> bool:
    return _call_started(tuple())


def start_voice_call(contact: str) -> str:
    return _start_call(contact, video=False)


def start_video_call(contact: str) -> str:
    return _start_call(contact, video=True)


def _start_call(contact: str, *, video: bool) -> str:
    label = "video" if video else "voice"
    try:
        with _ui():
            with whatsapp_front():
                r = _open_chat_unlocked(contact)
                if not r.startswith("ok"):
                    return r
                _maximize_whatsapp()
                time.sleep(0.2)
                if _click_header_call(video=video, contact=contact):
                    return f"Calling {contact} on WhatsApp ({label})."
                return (
                    f"Opened the chat with {contact}, but I could not start the {label} call. "
                    "Click the phone or camera icon in the WhatsApp header."
                )
    except Exception as e:
        return _failsafe_msg(e) or f"Could not start the WhatsApp {label} call: {e}"


def get_incoming_call_state() -> dict[str, Any]:
    state = {"incoming": False, "kind": "", "title": ""}
    try:
        for w in _win32_windows(visible_only=False) if os.name == "nt" else []:
            t = (w.get("title") or "").strip()
            low = t.lower()
            if not t:
                continue
            if "incoming" in low:
                kind = "video" if "video" in low else "voice"
                return {"incoming": True, "kind": kind, "title": t}
            if "whatsapp" in low and any(k in low for k in ("incoming", "is calling", "ringing")):
                kind = "video" if "video" in low else "voice"
                return {"incoming": True, "kind": kind, "title": t}
    except Exception:
        pass
    if _GW:
        try:
            for w in gw.getAllWindows():
                t = (w.title or "").strip()
                low = t.lower()
                if not t:
                    continue
                if "incoming" in low:
                    kind = "video" if "video" in low else "voice"
                    return {"incoming": True, "kind": kind, "title": t}
                if "whatsapp" in low and any(k in low for k in ("incoming", "is calling", "ringing")):
                    kind = "video" if "video" in low else "voice"
                    return {"incoming": True, "kind": kind, "title": t}
        except Exception:
            pass
    if _button_exists(_ACCEPT_BTN, timeout=0.2):
        kind = "video" if _button_exists(_VIDEO_BTN, timeout=0.15) else "voice"
        return {"incoming": True, "kind": kind, "title": "WhatsApp"}
    return state


def accept_call() -> str:
    err = _require()
    if err:
        return err
    try:
        with _ui():
            _focus_whatsapp_window()
            if _invoke_button(_ACCEPT_BTN):
                return "Accepted the WhatsApp call."
            return (
                "I could not find an Accept button. "
                "If a call is ringing, click the green button in WhatsApp."
            )
    except Exception as e:
        return _failsafe_msg(e) or f"Could not accept the call: {e}"


def decline_call() -> str:
    err = _require()
    if err:
        return err
    try:
        with _ui():
            _focus_whatsapp_window()
            if _invoke_button(_DECLINE_BTN):
                return "Declined the WhatsApp call."
            return (
                "I could not find a Decline button. "
                "If a call is ringing, click the red button in WhatsApp."
            )
    except Exception as e:
        return _failsafe_msg(e) or f"Could not decline the call: {e}"


def decline_with_message(contact_hint: str, message: str) -> str:
    msg = (message or "").strip() or str(load_prefs().get("auto_reply_text") or "")
    declined = decline_call()
    contact = (contact_hint or "").strip()
    if not msg:
        return declined
    if not contact:
        return declined + " No contact given, so I did not send an auto-reply."
    time.sleep(0.6)
    sent = open_chat_and_send(contact, msg)
    return f"{declined} {sent}"


def copy_latest_chat_snippet() -> str:
    """Read recent chat text via UIA. Clipboard is last resort and is restored."""
    err = _require()
    if err:
        return ""
    pane = _chat_pane_rect()
    pl = int(pane[0]) if pane else 0
    pt = int(pane[1]) if pane else 0
    pb = int(pane[1] + pane[3]) if pane else 0
    try:
        win = _whatsapp_uia_window()
        windows = [win] if win is not None else []
        if not windows:
            for w in _uia_windows():
                windows.append(w)
        for w in windows:
            if w is None:
                continue
            chunks: list[tuple[int, str]] = []
            try:
                for item in w.descendants(control_type="DataItem")[-20:]:
                    t = (item.window_text() or "").strip()
                    if not t or len(t) < 2:
                        continue
                    r = item.rectangle()
                    if pane and r.right < pl + 12:
                        continue
                    if pane and (r.bottom < pt or r.top > pb):
                        continue
                    chunks.append((int(r.top), t))
            except Exception:
                pass
            if not chunks:
                try:
                    for item in w.descendants(control_type="ListItem")[-12:]:
                        t = (item.window_text() or "").strip()
                        if not t:
                            continue
                        r = item.rectangle()
                        if pane and r.right < pl + 12:
                            continue
                        chunks.append((int(r.top), t))
                except Exception:
                    pass
            if chunks:
                chunks.sort(key=lambda x: x[0])
                return "\n".join(t for _y, t in chunks[-6:])
            try:
                texts = []
                for c in w.descendants(control_type="Text")[-16:]:
                    t = (c.window_text() or "").strip()
                    if t and len(t) > 1:
                        r = c.rectangle()
                        if pane and r.right < pl + 12:
                            continue
                        texts.append(t)
                if texts:
                    return "\n".join(texts[-6:])
            except Exception:
                pass
    except Exception:
        pass
    if not _CLIP:
        return ""
    old = None
    try:
        old = pyperclip.paste()
    except Exception:
        old = None
    try:
        with _ui():
            if not _focus_whatsapp_window():
                return ""
            rect = _whatsapp_rect()
            if not rect:
                return ""
            left, top, width, height = rect
            pyautogui.click(left + int(width * 0.62), top + int(height * 0.62))
            time.sleep(0.08)
            osn = _os_name()
            pyautogui.hotkey("shift", "up")
            pyautogui.hotkey("shift", "up")
            copy = ("command", "c") if osn == "mac" else ("ctrl", "c")
            pyautogui.hotkey(*copy)
            time.sleep(0.15)
            snippet = (pyperclip.paste() or "").strip()
            return snippet
    except Exception:
        return ""
    finally:
        if old is not None and _CLIP:
            try:
                pyperclip.copy(old)
            except Exception:
                pass


def draft_reply_with_gemini(incoming: str, *, persona: str = "") -> str:
    incoming = (incoming or "").strip()
    if not incoming:
        return ""
    try:
        from google import genai

        from memory.config_manager import get_gemini_key

        key = get_gemini_key() or ""
        if not key:
            return ""
        client = genai.Client(api_key=key)
        style = persona or load_prefs().get("chat_persona") or "polite and brief"
        prompt = (
            "You are texting on WhatsApp on behalf of the user.\n"
            f"Style: {style}\n"
            "Write ONE short reply (1-2 sentences). No quotes, no preamble, no emoji overload.\n"
            "If the incoming text looks like OUR previous reply, output exactly SKIP.\n\n"
            f"Incoming:\n{incoming[:1500]}"
        )
        resp = client.models.generate_content(model="gemini-flash-latest", contents=prompt)
        text = (getattr(resp, "text", None) or "").strip()
        line = text.split("\n")[0].strip().strip('"')
        if not line or line.upper() == "SKIP":
            return ""
        return line
    except Exception as e:
        print(f"[WhatsApp] draft_reply failed: {e}")
        return ""


def set_incoming_callback(cb: Optional[Callable[[dict], None]]) -> None:
    global _on_incoming
    _on_incoming = cb


def start_call_watcher(*, poll_sec: float = 1.8) -> str:
    global _call_thread, _last_call_state
    err = _require()
    if err:
        return err
    save_prefs(calls_armed=True)
    if _call_thread and _call_thread.is_alive():
        return "Call watcher already running."
    _call_stop.clear()
    _last_call_state = False

    def _loop():
        global _last_call_state
        while not _call_stop.wait(poll_sec):
            prefs = load_prefs()
            if not prefs.get("calls_armed"):
                continue
            try:
                st = get_incoming_call_state()
            except Exception:
                continue
            incoming = bool(st.get("incoming"))
            if incoming and not _last_call_state:
                action = str(prefs.get("default_call_action") or "banner").lower()
                print(f"[WhatsApp] Incoming call detected: {st}")
                if action == "decline":
                    decline_with_message("", str(prefs.get("auto_reply_text") or ""))
                elif action == "accept":
                    accept_call()
                if _on_incoming:
                    try:
                        _on_incoming(st)
                    except Exception:
                        pass
            _last_call_state = incoming

    _call_thread = threading.Thread(target=_loop, name="friday-wa-calls", daemon=True)
    _call_thread.start()
    return "Call watcher armed. I'll watch for incoming WhatsApp calls."


def stop_call_watcher() -> str:
    save_prefs(calls_armed=False)
    _call_stop.set()
    t = _call_thread
    if t and t.is_alive():
        t.join(timeout=3.0)
    return "Call watcher disarmed."


def start_chat_automation(
    contact: str,
    *,
    persona: str = "",
    draft_only: bool = True,
    player=None,
) -> str:
    from actions.chat_watch import start as _start

    return _start(
        contact,
        app="whatsapp",
        persona=persona,
        draft_only=draft_only,
        player=player,
    )


def stop_chat_automation() -> str:
    from actions.chat_watch import stop as _stop

    return _stop()


__all__ = [
    "accept_call",
    "copy_latest_chat_snippet",
    "conversation_is_empty",
    "dpi_scale_from",
    "physical_hwnd_rect",
    "decline_call",
    "decline_with_message",
    "draft_reply_with_gemini",
    "ensure_whatsapp_open",
    "get_incoming_call_state",
    "load_prefs",
    "looks_like_group",
    "open_chat",
    "open_chat_and_send",
    "save_prefs",
    "send_chat_text",
    "set_incoming_callback",
    "start_call_watcher",
    "start_chat_automation",
    "whatsapp_front",
    "start_video_call",
    "start_voice_call",
    "stop_call_watcher",
    "stop_chat_automation",
]
