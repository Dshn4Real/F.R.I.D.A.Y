"""
core/killswitch.py — a system-wide emergency stop for all automation.

WHY THE HUD'S STOP BUTTON IS NOT ENOUGH
    While FRIDAY drives the mouse and keyboard (computer_control, browser
    automation), focus is wherever it is clicking — which can be exactly the
    thing preventing you from reaching the HUD. The escape hatch must work
    regardless of focus, so it is a GLOBAL hotkey registered with the Win32
    RegisterHotKey API:

        Ctrl + Alt + K   →   halt the agent task, stop speech, log it.

    (pyautogui's own FAILSAFE — slam the mouse into the top-left corner —
    remains active as the second, zero-software escape hatch.)

WHAT IT DOES AND DOES NOT DO
    It cancels the running agent task between steps and interrupts speech.
    It does NOT try to undo what already happened (that is the undo tool) and
    does NOT kill the process — FRIDAY stays alive to take the next order.

IMPLEMENTATION NOTES
    RegisterHotKey binds to the calling thread, so registration and the
    GetMessage loop live on one dedicated daemon thread. If another app
    already owns Ctrl+Alt+K, registration fails and we log and carry on —
    a missing luxury exit must never block startup.
"""

from __future__ import annotations

import platform
import threading
from typing import Callable, Optional

HOTKEY_LABEL = "Ctrl+Alt+K"

_MOD_ALT      = 0x0001
_MOD_CONTROL  = 0x0002
_MOD_NOREPEAT = 0x4000
_VK_K         = 0x4B
_WM_HOTKEY    = 0x0312
_HOTKEY_ID    = 0xF12A


def start(on_trigger: Callable[[], None],
          log: Optional[Callable[[str], None]] = None) -> bool:
    """Register the global kill-switch hotkey. Returns True when armed.
    Non-Windows platforms and registration conflicts return False silently
    (logged when a logger is given) — the feature is an extra guard rail,
    not a dependency."""
    if platform.system() != "Windows":
        return False

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    armed = threading.Event()
    failed = threading.Event()

    def _loop():
        if not user32.RegisterHotKey(
            None, _HOTKEY_ID, _MOD_CONTROL | _MOD_ALT | _MOD_NOREPEAT, _VK_K
        ):
            failed.set()
            armed.set()
            return
        armed.set()
        msg = wintypes.MSG()
        # GetMessageW blocks; the thread is daemon so process exit is clean.
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == _WM_HOTKEY and msg.wParam == _HOTKEY_ID:
                try:
                    on_trigger()
                except Exception as e:
                    if log:
                        log(f"ERR: Kill switch handler failed — {e}")

    threading.Thread(target=_loop, daemon=True, name="kill-switch").start()
    armed.wait(3.0)

    if failed.is_set():
        if log:
            log(f"SYS: Kill switch hotkey {HOTKEY_LABEL} is taken by another "
                f"app — HUD STOP button still works.")
        return False
    if log:
        log(f"SYS: Kill switch armed — {HOTKEY_LABEL} halts all automation.")
    return True
