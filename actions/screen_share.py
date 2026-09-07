"""Live screen share for Gemini Live.

Gemini Live does not take a video codec. A stream of JPEG frames sent as
`send_realtime_input(video=...)` at ~1 FPS is the live-share path.
This module holds capture + start/stop state so the Live loop stays thin
and the behaviour can be tested without Qt or a network session.
"""
from __future__ import annotations

import time
from actions.screen_processor import _capture_screen

# Google's Live screen-share path is ~1 frame per second.
FRAME_INTERVAL = 1.0
_SHARE_MAX_W = 960
_SHARE_MAX_H = 540
_SHARE_JPEG_Q = 72

STARTED = "started"
ALREADY = "already"
STOPPED = "stopped"
IDLE = "idle"
UNKNOWN = "unknown"

_START_WORDS = frozenset({"start", "on", "begin", "share", "watch", "live"})
_STOP_WORDS = frozenset({"stop", "off", "end", "close", "quit"})
_STATUS_WORDS = frozenset({"status", "state"})


def normalize_action(action: str) -> str:
    raw = (action or "start").strip().lower()
    if raw in _STOP_WORDS:
        return "stop"
    if raw in _STATUS_WORDS:
        return "status"
    if raw in _START_WORDS or not raw:
        return "start"
    return raw


def capture_frame() -> tuple[bytes, str]:
    """Grab the primary display as a compact JPEG for the Live video channel."""
    return _capture_screen(max_w=_SHARE_MAX_W, max_h=_SHARE_MAX_H, quality=_SHARE_JPEG_Q)


class ShareState:
    """Idempotent latch for whether a live share is supposed to be running."""

    def __init__(self) -> None:
        self.active = False
        self.frames_sent = 0
        self.started_at = 0.0

    def start(self) -> str:
        if self.active:
            return ALREADY
        self.active = True
        self.frames_sent = 0
        self.started_at = time.monotonic()
        return STARTED

    def stop(self) -> str:
        if not self.active:
            return IDLE
        self.active = False
        return STOPPED

    def mark_frame(self) -> None:
        self.frames_sent += 1


def apply_action(state: ShareState, action: str) -> tuple[str, str]:
    """Apply start/stop/status. Returns (code, spoken-style result)."""
    kind = normalize_action(action)
    if kind == "status":
        if state.active:
            return "status", (
                "Live screen share is ON — you are already receiving the "
                "user's display. Do not call screen_process."
            )
        return "status", "Live screen share is OFF."
    if kind == "stop":
        code = state.stop()
        if code == IDLE:
            return IDLE, "Screen share is not running."
        return STOPPED, (
            "[SCREEN_SHARE_STOPPED] Live screen feed ended. "
            "You can no longer see the screen unless you call screen_process "
            "or start sharing again. Confirm in one short sentence."
        )
    if kind == "start":
        code = state.start()
        if code == ALREADY:
            return ALREADY, (
                "Live screen share is already running. You can already see "
                "the user's screen — do not call screen_process."
            )
        return STARTED, (
            "[SCREEN_SHARE_ACTIVE] Live screen feed started. You now receive "
            "the user's display continuously at about one frame per second. "
            "Answer questions about what you see without calling screen_process. "
            "When they say stop sharing, call screen_share action=stop. "
            "Confirm in one short sentence that you are watching the screen."
        )
    return UNKNOWN, f"Unknown screen_share action '{action}'. Use start, stop, or status."


def sleep_remaining(started: float, interval: float = FRAME_INTERVAL) -> float:
    """Seconds to wait before the next frame so we hold ~1 FPS including capture."""
    return max(0.0, interval - (time.monotonic() - started))
