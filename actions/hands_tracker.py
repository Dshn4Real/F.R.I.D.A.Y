"""Local webcam hand board — MediaPipe 21-point skeleton in the HUD.

Ports the fullstack-agent (barehands) camera-control loop: pinch/drag,
tap, two-hand scale, hold-to-rotate, flick, clap reset, claw force-pull,
present/yank/hover, camera cycle, debug overlay. Drawing is OpenCV so
the HUD only has to show the camera frame.
"""

from __future__ import annotations

import math
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from actions.hands_gestures import (
    CLAP_APART,
    CLAP_MCP,
    CLAP_WRIST,
    FLICK_FOLLOW,
    FLICK_PEAK,
    GHOST_SPEED,
    HEAL_SPEED,
    PINCH_OFF_FRAMES,
    PINCH_ON,
    PINCH_ON_FRAMES,
    Cursor,
    claw_snap,
    decide_pinch,
    measure,
    peak_velocity,
    pinch_ratio,
    speed,
)

try:
    import cv2
    _CV2 = True
except ImportError:
    _CV2 = False


MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
)
FRICTION = 0.90
CARD_W, CARD_H = 0.24, 0.18
GRAB_PAD = 0.05
_WRIST, _THUMB, _INDEX = 0, 4, 8

_CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (0, 9), (9, 10), (10, 11), (11, 12),
    (0, 13), (13, 14), (14, 15), (15, 16),
    (0, 17), (17, 18), (18, 19), (19, 20),
    (5, 9), (9, 13), (13, 17),
)

_UID = 0
_MEDIA_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".glb", ".gltf", ".obj"}


def _root() -> Path:
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def model_path() -> Path:
    return _root() / "data" / "models" / "hand_landmarker.task"


def ensure_model() -> Path:
    path = model_path()
    if path.is_file() and path.stat().st_size > 1000:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(MODEL_URL, path)
    if not path.is_file() or path.stat().st_size < 1000:
        raise RuntimeError("Hand model download was incomplete.")
    return path


def _next_uid() -> int:
    global _UID
    _UID += 1
    return _UID


def media_roots() -> list[Path]:
    home = Path.home()
    names = (
        "Desktop", "Downloads", "Pictures", "Documents", "Videos",
        "OneDrive/Desktop", "OneDrive/Downloads", "OneDrive/Pictures",
        "OneDrive/Documents",
    )
    out: list[Path] = []
    seen: set[str] = set()
    for raw in [home, *(home / n for n in names)]:
        try:
            p = raw.resolve()
        except OSError:
            continue
        if not p.is_dir():
            continue
        key = str(p).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def resolve_media_path(hint: str = "", current: str = "", roots: list[Path] | None = None) -> str:
    """Find a local image/model without copying it into FRIDAY."""
    hint = (hint or "").strip().strip('"').strip("'")
    current = (current or "").strip().strip('"').strip("'")

    def _if_file(raw: str) -> str:
        if not raw:
            return ""
        p = Path(raw).expanduser()
        try:
            if p.is_file():
                return str(p.resolve())
        except OSError:
            return ""
        return ""

    hit = _if_file(hint)
    if hit:
        return hit
    if hint:
        name = Path(hint).name.lower()
        found: list[Path] = []

        def _scan(root: Path, depth: int) -> None:
            if found or depth < 0:
                return
            try:
                children = list(root.iterdir())
            except OSError:
                return
            for child in children:
                if child.name.startswith("."):
                    continue
                try:
                    if child.is_file() and child.suffix.lower() in _MEDIA_EXTS and child.name.lower() == name:
                        found.append(child)
                    elif child.is_dir() and depth > 0:
                        _scan(child, depth - 1)
                except OSError:
                    continue

        for root in (roots if roots is not None else media_roots()):
            _scan(root, 3)
            if found:
                break
        if not found and "/" not in hint.replace("\\", "/") and not Path(hint).suffix:
            needle = hint.lower()
            for root in (roots if roots is not None else media_roots()):
                try:
                    children = list(root.iterdir())
                except OSError:
                    continue
                for child in children:
                    if not child.is_file() or child.suffix.lower() not in _MEDIA_EXTS:
                        continue
                    if child.stem.lower() == needle:
                        found.append(child)
                if found:
                    break
        if found:
            found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return str(found[0].resolve())
        return hint
    hit = _if_file(current)
    if hit:
        return hit
    return current
    global _UID
    _UID += 1
    return _UID


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


@dataclass
class Card:
    title: str
    body: str
    x: float
    y: float
    w: float = CARD_W
    h: float = CARD_H
    vx: float = 0.0
    vy: float = 0.0
    grabbed: bool = False
    image: str = ""
    kind: str = "card"
    uid: int = 0
    scale: float = 1.0
    rx: float = 0.0
    ry: float = 0.0
    open: bool = False
    presented: bool = False
    flying: bool = False
    mode: str = "move"
    grabbed_by: list = field(default_factory=list)
    ox: float = 0.0
    oy: float = 0.0
    hold_x: float = 0.0
    hold_y: float = 0.0
    hold_t: float = 0.0
    tap_x: float = 0.0
    tap_y: float = 0.0
    stretch: Optional[tuple] = None
    stretch_t: float = 0.0
    anim: Optional[dict] = None
    hch: float = 0.0
    jx: float = 0.0
    jy: float = 0.0
    in_s: float = 1.0
    pull_t: float = 0.0
    scroll_y: float = 0.0
    scroll0: float = 0.0
    grab_y0: float = 0.0
    grab_x0: float = 0.0
    bar_grab: bool = False
    rot0x: float = 0.0
    rot0y: float = 0.0
    model: str = ""
    holo: bool = True
    ex: float = 0.0
    rvy: float = 0.0

    def contains(self, px: float, py: float) -> bool:
        hw = self.w * self.scale / 2 + GRAB_PAD
        hh = self.h * self.scale / 2 + GRAB_PAD
        return self.x - hw <= px <= self.x + hw and self.y - hh <= py <= self.y + hh

    def clamp(self) -> None:
        if self.flying:
            return
        self.x = min(0.92, max(0.08, self.x))
        self.y = min(0.90, max(0.12, self.y))


@dataclass
class HandsBoard:
    cards: list[Card] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.RLock)
    _cursors: dict[int, Cursor] = field(default_factory=dict)
    _clap_hist: list = field(default_factory=list)
    _clap_until: float = 0.0
    _clear_until: float = 0.0
    _last_cursor: tuple[float, float] = (0.50, 0.48)
    camera_index: int = 0
    debug: bool = False
    ring_state: str = "listening"
    _toast: str = ""
    _toast_until: float = 0.0
    _cam_cycle: bool = False
    _presented: Optional[int] = None

    def toast(self, text: str, hold: float = 1.4) -> None:
        self._toast = text
        self._toast_until = time.monotonic() + hold

    def banner(self) -> str:
        if time.monotonic() < self._toast_until:
            return self._toast
        if self.debug:
            return "DEBUG  ·  C camera  ·  R reset  ·  D overlay"
        return "Drop a file on the glass · tap FRIDAY · pinch · clap reset · O open"

    def set_ring_state(self, state: str) -> None:
        key = (state or "").strip().lower()
        self.ring_state = {
            "speaking": "speaking",
            "listening": "listening",
            "thinking": "thinking",
            "idle": "idle",
            "sleeping": "idle",
            "muted": "idle",
        }.get(key, "listening")

    def cycle_camera(self) -> None:
        self._cam_cycle = True
        self.toast("cycling camera…")

    def consume_cam_cycle(self) -> bool:
        flag = self._cam_cycle
        self._cam_cycle = False
        return flag

    def toggle_debug(self) -> None:
        self.debug = not self.debug
        self.toast("debug ON" if self.debug else "debug OFF")

    def _spawn_ring(self, cx: float = 0.30, cy: float = 0.45) -> Card:
        ring = Card("FRIDAY", "tap to bloom", cx, cy, 0.28, 0.28, kind="ring", uid=_next_uid())
        self.cards.append(ring)
        return ring

    def reset(self) -> None:
        with self._lock:
            self.cards = []
            self._cursors = {}
            self._clap_hist = []
            self._presented = None
            self._spawn_ring(0.50, 0.42)
        self.toast("clap — ring center stage")

    def reset_starter(self) -> None:
        with self._lock:
            self.cards = []
            self._cursors = {}
            self._clap_hist = []
            self._presented = None
            self._spawn_ring(0.22, 0.48)
            self.cards.extend([
                Card("PINCH", "Thumb + index to grab. Quick tap opens.", 0.50, 0.32, uid=_next_uid()),
                Card("SCALE", "Two pinches stretch. Hold still to rotate.", 0.72, 0.48, uid=_next_uid()),
                Card("CLAW", "Flash open, claw, aim, hold 2s, snap shut.", 0.50, 0.68, uid=_next_uid()),
            ])

    def present(self, title: str, body: str) -> None:
        title = (title or "FRIDAY").strip() or "FRIDAY"
        body = (body or "").strip()
        with self._lock:
            hit = self._find_unlocked(title)
            if hit is None:
                hit = Card(title, body, 0.50, 0.44, 0.32, 0.24, uid=_next_uid())
                self.cards.append(hit)
            self._spotlight(hit)

    def add_card(self, title: str, body: str, x: float | None = None, y: float | None = None) -> None:
        title = (title or "Note").strip() or "Note"
        body = (body or "").strip()
        n = len(self.cards)

        def _frac(v, fallback):
            try:
                if v is None:
                    return fallback
                return min(0.92, max(0.08, float(v)))
            except (TypeError, ValueError):
                return fallback

        with self._lock:
            cx = _frac(x, 0.22 + (n % 3) * 0.28)
            cy = _frac(y, 0.32 + (n // 3) * 0.28)
            self.cards.append(Card(title, body, cx, cy, uid=_next_uid(), anim={"k": "in", "t": 0.0}))

    def add_image(self, path: str, title: str = "") -> None:
        from actions.hands_models import is_model_path
        resolved = resolve_media_path(path)
        if resolved:
            path = resolved
        if is_model_path(path):
            self.add_model(path, title)
            return
        p = Path(path)
        title = (title or p.stem or "Image").strip()
        n = len(self.cards)
        x = 0.28 + (n % 3) * 0.28
        y = 0.42 + (n // 3) * 0.22
        with self._lock:
            self.cards.append(Card(title, "", x, y, 0.34, 0.30, image=str(p), kind="img", uid=_next_uid(), anim={"k": "in", "t": 0.0}))

    def present_image(self, path: str, title: str = "") -> None:
        from actions.hands_models import is_model_path
        resolved = resolve_media_path(path)
        if resolved:
            path = resolved
        if is_model_path(path):
            self.present_model(path, title)
            return
        p = Path(path)
        title = (title or p.stem or "Image").strip()
        with self._lock:
            hit = Card(title, "", 0.50, 0.44, 0.42, 0.36, image=str(p), kind="img", uid=_next_uid())
            self.cards.append(hit)
            self._spotlight(hit)

    def hand_item(self, title: str, body: str = "", image: str = "") -> None:
        from actions.hands_models import is_model_path
        x, y = self._last_cursor
        resolved = resolve_media_path(image) if image else ""
        if resolved:
            image = resolved
        if image and is_model_path(image):
            self.add_model(image, title, x=x, y=y)
            return
        with self._lock:
            if image:
                card = Card(title or Path(image).stem, "", x, y, 0.34, 0.30, image=image, kind="img", uid=_next_uid())
            else:
                card = Card(title or "Note", body, x, y, uid=_next_uid())
            card.anim = {"k": "in", "t": 0.0}
            self.cards.append(card)

    def add_model(self, path: str = "", title: str = "", x: float | None = None, y: float | None = None) -> None:
        from actions.hands_models import load_model, wants_holo
        resolved = resolve_media_path(path) if path else ""
        if resolved:
            path = resolved
        p = Path(path) if path else Path("demo-engine")
        title = (title or p.stem or "MODEL").strip()
        n = len(self.cards)
        cx = x if x is not None else 0.50
        cy = y if y is not None else 0.46
        if x is None:
            cx = 0.30 + (n % 2) * 0.36
        src = path or "demo://engine"
        err = ""
        try:
            load_model(src)
        except Exception as e:
            err = str(e)
            print(f"[Hands] {err}")
            src = "demo://engine"
            load_model(src)
        holo = True if err else (wants_holo(path) if path else True)
        with self._lock:
            self.cards.append(Card(
                title, "pinch to turn · two hands scale · empty pinch scrubs explode",
                cx, cy, 0.42, 0.42, kind="model", uid=_next_uid(),
                model=src, holo=holo,
                anim={"k": "in", "t": 0.0},
            ))
        if err:
            self.toast(f"couldn't read {p.name}: {err[:80]}")
        else:
            self.toast(f"3D {title} — hold still to spin, empty pinch scrubs explode")

    def present_model(self, path: str = "", title: str = "") -> None:
        from actions.hands_models import load_model, wants_holo
        resolved = resolve_media_path(path) if path else ""
        if resolved:
            path = resolved
        p = Path(path) if path else Path("demo-engine")
        title = (title or p.stem or "MODEL").strip()
        src = path or "demo://engine"
        try:
            load_model(src)
        except Exception as e:
            print(f"[Hands] {e}")
            src = "demo://engine"
            load_model(src)
            self.toast(f"couldn't read {p.name}: {str(e)[:80]}")
        with self._lock:
            hit = Card(
                title, "", 0.50, 0.44, 0.44, 0.44, kind="model", uid=_next_uid(),
                model=src, holo=wants_holo(path) if path else True,
            )
            self.cards.append(hit)
            self._spotlight(hit)

    def explode(self, title: str = "") -> str:
        with self._lock:
            hit = self._find_unlocked(title) if title else None
            if hit is None or hit.kind != "model":
                hit = next((c for c in reversed(self.cards) if c.kind == "model"), None)
            if hit is None:
                return "No 3D model on the board."
            hit.ex = 1.0
            self._presented = hit.uid
        self.toast("explode")
        return f"Exploded '{hit.title}'."

    def assemble(self, title: str = "") -> str:
        with self._lock:
            hit = self._find_unlocked(title) if title else None
            if hit is None or hit.kind != "model":
                hit = next((c for c in reversed(self.cards) if c.kind == "model"), None)
            if hit is None:
                return "No 3D model on the board."
            hit.ex = 0.0
        self.toast("assemble")
        return f"Assembled '{hit.title}'."

    def yank(self, title: str) -> None:
        with self._lock:
            hit = self._find_unlocked(title)
            if not hit:
                return
            hit.grabbed_by = []
            hit.grabbed = False
            hit.flying = False
            hit.anim = {"k": "yank", "t": 0.0}

    def hover(self, title: str) -> None:
        with self._lock:
            hit = self._find_unlocked(title)
            if hit:
                hit.anim = {"k": "hover", "t": 0.0}

    def clear(self) -> None:
        with self._lock:
            self.cards = []
            self._cursors = {}
            self._presented = None
            self._clap_hist = []

    def snapshot(self) -> list[Card]:
        with self._lock:
            out = []
            for c in self.cards:
                copy = Card(
                    title=c.title, body=c.body, x=c.x, y=c.y, w=c.w, h=c.h,
                    vx=c.vx, vy=c.vy, grabbed=bool(c.grabbed_by), image=c.image,
                    kind=c.kind, uid=c.uid, scale=c.scale, rx=c.rx, ry=c.ry,
                    open=c.open, presented=c.presented, flying=c.flying, mode=c.mode,
                )
                copy.jx, copy.jy, copy.hch, copy.in_s = c.jx, c.jy, c.hch, c.in_s
                copy.scroll_y = c.scroll_y
                copy.model, copy.holo, copy.ex = c.model, c.holo, c.ex
                out.append(copy)
            return out

    def _find_unlocked(self, title: str) -> Optional[Card]:
        want = (title or "").strip().lower()
        if not want:
            return None
        for c in self.cards:
            if c.title.lower() == want:
                return c
        return None

    def _spotlight(self, hit: Card) -> None:
        for c in self.cards:
            c.presented = False
        hit.presented = True
        hit.anim = {
            "k": "present", "t": 0.0,
            "x0": hit.x, "y0": hit.y,
            "s0": hit.scale, "s1": min(2.2, max(1.25, hit.scale * 1.6)),
        }
        self._presented = hit.uid
        self.toast(f"presenting {hit.title}")

    def _end_present(self) -> None:
        self._presented = None
        for c in self.cards:
            c.presented = False

    def _hit(self, px: float, py: float) -> Optional[Card]:
        for card in reversed(self.cards):
            if card.contains(px, py):
                return card
        return None

    def _begin_grab(self, card: Card, i: int, cur: Cursor, now: float) -> None:
        card.flying = False
        card.anim = None
        if i not in card.grabbed_by:
            card.grabbed_by.append(i)
        card.grabbed = True
        cur.grab_t = now
        if self._presented == card.uid:
            self._end_present()
        if len(card.grabbed_by) == 1:
            card.ox = card.x - cur.x
            card.oy = card.y - cur.y
            card.tap_x, card.tap_y = cur.x, cur.y
            card.hold_x, card.hold_y, card.hold_t = cur.x, cur.y, now
            if card.kind == "ring":
                card.mode = "move"
            elif card.open:
                bar = py_on_bar(card, cur.y)
                if bar:
                    card.mode = "move"
                    card.bar_grab = True
                else:
                    card.mode = "scroll"
                    card.bar_grab = False
                    card.scroll0 = card.scroll_y
                    card.grab_y0 = cur.y
            else:
                card.mode = "move"
                card.bar_grab = False
        elif len(card.grabbed_by) == 2:
            a = self._cursors.get(card.grabbed_by[0])
            b = self._cursors.get(card.grabbed_by[1])
            if a and b:
                card.stretch = (math.hypot(a.x - b.x, a.y - b.y) or 1e-6, card.scale)
                card.mode = "stretch"

    def _end_grab(self, card: Card, i: int, cur: Cursor, now: float) -> None:
        card.grabbed_by = [k for k in card.grabbed_by if k != i]
        if len(card.grabbed_by) == 1:
            card.stretch = None
            rem = self._cursors.get(card.grabbed_by[0])
            if rem:
                card.ox, card.oy = card.x - rem.x, card.y - rem.y
                card.mode = "move"
                card.hold_x, card.hold_y, card.hold_t = rem.x, rem.y, now
            return
        card.grabbed = False
        card.stretch = None
        card.hch = 0.0
        grip = now - (cur.grab_t or now)
        travel = _dist((cur.x, cur.y), (card.tap_x, card.tap_y))
        tap = grip < 0.35 and travel < 0.03 and not cur.prob_kill
        pull_fresh = now - card.pull_t < 0.80
        if tap and not pull_fresh:
            if card.kind == "ring":
                self._ring_toggle(card)
            elif card.kind == "orb":
                self._open_orb(card)
            elif card.open and card.bar_grab:
                self._kill(card)
            elif card.kind == "card" and not card.open:
                card.open = True
                card.h, card.w = 0.42, 0.36
            elif card.kind in ("img", "model") and not card.flying:
                self._kill(card)
        card.bar_grab = False
        flung = False
        if (
            len(cur.history) >= 2
            and grip >= 0.12
            and now - card.stretch_t > 0.70
            and card.mode != "rotate"
            and card.kind != "ring"
        ):
            pk, vx, vy, last_s = peak_velocity(cur.history)
            if pk > FLICK_PEAK and last_s > pk * FLICK_FOLLOW:
                if not (card.open and card.mode == "scroll" and abs(vx) <= abs(vy) * 1.2):
                    card.vx, card.vy = vx, vy
                    card.flying = True
                    flung = True
        if not flung:
            card.vx = card.vy = 0.0

    def _kill(self, card: Card) -> None:
        if card.anim and card.anim.get("k") == "out":
            return
        card.grabbed_by = []
        card.grabbed = False
        card.flying = False
        card.anim = {"k": "out", "t": 0.0}

    def _ring_toggle(self, ring: Card) -> None:
        orbs = [c for c in self.cards if c.kind == "orb"]
        if orbs:
            for o in orbs:
                self._kill(o)
            return
        defs = [
            ("PINCH", "Thumb + index. Quick tap opens."),
            ("CLAW", "Open flash, claw, aim, 2s, snap."),
            ("CLAP", "Palms together, fingers up."),
        ]
        for i, (title, body) in enumerate(defs):
            ang = (i - 1) * 0.96
            o = Card(title, body, ring.x + 0.22 * math.cos(ang), ring.y + 0.18 * math.sin(ang),
                     kind="orb", uid=_next_uid(), anim={"k": "in", "t": 0.0})
            self.cards.append(o)

    def _open_orb(self, orb: Card) -> None:
        panel = Card(orb.title, orb.body, min(0.78, orb.x + 0.18), orb.y, 0.36, 0.42,
                     kind="card", uid=_next_uid(), open=True, anim={"k": "in", "t": 0.0})
        self.cards.append(panel)
        self._kill(orb)

    def tick(self, hands: list, dt: float) -> dict:
        now = time.monotonic()
        metrics = [measure(h) for h in hands]
        metrics = [m for m in metrics if m]
        hint = {
            "pinch": None, "clapping": False, "claw": False, "debug": "",
            "ratio": None, "cursor": None,
        }
        with self._lock:
            seen = set()
            for i, m in enumerate(metrics[:2]):
                seen.add(i)
                cur = self._cursors.get(i) or Cursor(x=m.cursor[0], y=m.cursor[1])
                cur.x += (m.cursor[0] - cur.x) * 0.45
                cur.y += (m.cursor[1] - cur.y) * 0.45
                self._last_cursor = (cur.x, cur.y)
                cur.history.append((cur.x, cur.y, now))
                cur.history = cur.history[-10:]
                cur.wrist, cur.mcp = m.wrist, m.mcp
                cur.hand_up = m.hand_up
                cur.palm_open, cur.soft_open = m.palm_open, m.soft_open
                cur.ratio = m.ratio
                holding = any(i in c.grabbed_by for c in self.cards)
                was = cur.pinched
                cur.pinched = decide_pinch(cur, m, holding, now)
                if cur.pinched:
                    cur.last_pinch_t = now
                hint["ratio"] = m.ratio
                hint["cursor"] = (cur.x, cur.y)
                if cur.pinched:
                    hint["pinch"] = (cur.x, cur.y)

                if cur.pinched and not was:
                    cur.pinch_t = now
                    born = speed(cur.history, now)
                    cur.ghost = born > GHOST_SPEED
                    if not cur.ghost:
                        hit = self._hit(cur.x, cur.y)
                        if hit and len(hit.grabbed_by) < 2 and i not in hit.grabbed_by:
                            self._begin_grab(hit, i, cur, now)
                elif cur.pinched and cur.ghost:
                    if speed(cur.history, now) < HEAL_SPEED and not any(i in c.grabbed_by for c in self.cards):
                        cur.ghost = False
                        hit = self._hit(cur.x, cur.y)
                        if hit and len(hit.grabbed_by) < 2 and i not in hit.grabbed_by:
                            self._begin_grab(hit, i, cur, now)
                elif not cur.pinched and was:
                    held = next((c for c in self.cards if i in c.grabbed_by), None)
                    if held:
                        self._end_grab(held, i, cur, now)
                    cur.scrub = None

                self._tick_scrub(i, cur)

                if not cur.pinched:
                    if cur.palm_open or cur.soft_open:
                        cur.last_open_t = now
                    if cur.soft_open:
                        cur.last_soft_t = now

                self._tick_claw(i, cur, m, now, hint)
                self._tick_held(i, cur, now, dt)
                self._cursors[i] = cur

            for i in list(self._cursors):
                if i not in seen:
                    cur = self._cursors.pop(i)
                    for c in self.cards:
                        if i in c.grabbed_by:
                            self._end_grab(c, i, cur, now)

            hint["clapping"] = self._tick_clap(now)
            self._tick_physics(dt, now)
            if self.debug and metrics:
                m0 = metrics[0]
                cur = self._cursors.get(0)
                hint["debug"] = (
                    f"pinch {m0.ratio:.2f} {'CLOSED' if cur and cur.pinched else 'open'}  "
                    f"claw {m0.claw}  up {m0.hand_up:.2f}  cam {self.camera_index}"
                )
        return hint

    def _tick_scrub(self, i: int, cur: Cursor) -> None:
        holding = any(i in c.grabbed_by for c in self.cards)
        if cur.pinched and not holding:
            if not cur.scrub:
                models = [c for c in self.cards if c.kind == "model"]
                m = models[-1] if models else None
                cur.scrub = {
                    "id": m.uid if m else None,
                    "sx": cur.x,
                    "base": (m.ex if m else 0.0),
                    "live": False,
                }
            elif cur.scrub.get("id") is not None:
                m = next((c for c in self.cards if c.uid == cur.scrub["id"]), None)
                if m is not None:
                    dx = cur.x - cur.scrub["sx"]
                    if not cur.scrub["live"] and abs(dx) > 0.03:
                        cur.scrub["live"] = True
                        cur.scrub["sx"] = cur.x
                        cur.scrub["base"] = m.ex
                        self.toast(
                            "scrubbing — drag right to explode, left to rebuild"
                            if m.ex < 0.5
                            else "scrubbing — drag left to rebuild"
                        )
                    if cur.scrub["live"]:
                        m.ex = min(1.0, max(0.0, cur.scrub["base"] + (cur.x - cur.scrub["sx"]) / 0.34))
        elif not cur.pinched:
            cur.scrub = None

    def _tick_claw(self, i: int, cur: Cursor, m, now: float, hint: dict) -> None:
        holding = any(i in c.grabbed_by for c in self.cards)
        in_claw = cur.fp_ph > 0
        claw = m.claw_hold if in_claw else m.claw
        snap = claw_snap(cur, m.ratio, now)
        hint["claw"] = hint["claw"] or claw
        cur.dbg = f"r:{m.ratio:.2f} c:{m.c8:.2f}/{m.c12:.2f}/{m.c16:.2f}/{m.c20:.2f} a:{m.aspect:.2f}"
        cur.fp_pose = (cur.fp_pose + 1) if claw else max(0, cur.fp_pose - 5)
        hit_now = self._hit(cur.x, cur.y)

        def unlight():
            t = next((c for c in self.cards if c.uid == cur.fp_tid), None)
            if t and not t.grabbed_by:
                t.jx = t.jy = t.hch = 0.0
            cur.fp_tid = None

        if cur.fp_ph == 0:
            if not holding and cur.fp_pose >= 14 and hit_now is None and now - cur.last_open_t < 0.90:
                cur.fp_ph = 1
                cur.fp_lost = 0.0
                cur.fp_tid = None
            elif cur.fp_pose == 20 and not holding and now - cur.last_open_t >= 0.90:
                self.toast("claw ignored — flash OPEN first, then claw")
            return
        if claw:
            cur.fp_lost = 0.0
        elif not cur.fp_lost:
            cur.fp_lost = now
        if holding or (cur.fp_lost and not snap and now - cur.fp_lost > 0.30):
            unlight()
            cur.fp_ph = 0
            return
        if snap and cur.fp_tid is not None:
            if now - cur.fp_lit >= 2.0:
                t = next((c for c in self.cards if c.uid == cur.fp_tid), None)
                unlight()
                cur.fp_ph = 0
                if t and not t.grabbed_by:
                    t.hch = 0.6
                    t.anim = {"k": "pull", "t": 0.0, "x0": t.x, "y0": t.y, "ci": i}
            else:
                unlight()
                cur.fp_ph = 0
                self.toast("too soon — let it strain 2 seconds")
            return
        if not claw:
            return
        t4, t8 = m.pts[4], m.pts[8]
        p5, p17 = m.pts[5], m.pts[17]
        pcx, pcy = (p5[0] + p17[0]) / 2, (p5[1] + p17[1]) / 2
        dx, dy = -(t8[1] - t4[1]), t8[0] - t4[0]
        if dx * (cur.x - pcx) + dy * (cur.y - pcy) < 0:
            dx, dy = -dx, -dy
        dl = math.hypot(dx, dy) or 1.0
        dx, dy = dx / dl, dy / dl
        best, best_s = None, 1e9
        for t in self.cards:
            if t.grabbed_by or t.kind in ("orb",) or t.open:
                continue
            vx, vy = t.x - cur.x, t.y - cur.y
            if math.hypot(vx, vy) < 0.16:
                continue
            proj = vx * dx + vy * dy
            if proj <= 0:
                continue
            perp = abs(vx * dy - vy * dx)
            if perp > proj * 0.25:
                continue
            if perp < best_s:
                best_s, best = perp, t
        if best and best.uid != cur.fp_tid:
            unlight()
            cur.fp_tid = best.uid
            cur.fp_lit = now
            cur.fp_ready = False
            best.flying = False
            best.anim = None
            self.toast(f"aiming: {best.title} — hold the strain")
        elif not best and cur.fp_tid is not None:
            unlight()
        lit = next((c for c in self.cards if c.uid == cur.fp_tid), None)
        if lit:
            hold = min(1.0, (now - cur.fp_lit) / 4.0)
            amp = (4 + hold * 40) / 960.0
            lit.jx = (np.random.random() - 0.5) * amp * 2
            lit.jy = (np.random.random() - 0.5) * amp * 2
            lit.hch = 0.35 + hold * 0.65
            if not cur.fp_ready and now - cur.fp_lit >= 2.0:
                cur.fp_ready = True
                self.toast("it's straining — SNAP to take it")

    def _tick_held(self, i: int, cur: Cursor, now: float, dt: float) -> None:
        held = next((c for c in self.cards if i in c.grabbed_by), None)
        if not held:
            return
        if held.mode == "stretch" and held.stretch and len(held.grabbed_by) == 2:
            a = self._cursors.get(held.grabbed_by[0])
            b = self._cursors.get(held.grabbed_by[1])
            if a and b:
                d0, s0 = held.stretch
                d = math.hypot(a.x - b.x, a.y - b.y)
                held.scale = min(12.0, max(0.12, s0 * (d / d0)))
                held.stretch_t = now
                held.x = (a.x + b.x) / 2
                held.y = (a.y + b.y) / 2
        elif held.mode == "scroll" and held.grabbed_by[0] == i:
            held.scroll_y = min(0.35, max(0.0, held.scroll0 - (cur.y - held.grab_y0) * 1.4))
            drift = _dist((cur.x, cur.y), (held.hold_x, held.hold_y))
            if drift > 0.08:
                held.hold_x, held.hold_y, held.hold_t = cur.x, cur.y, now
                held.hch = 0.0
                held.scroll0 = held.scroll_y
                held.grab_y0 = cur.y
            else:
                p = min((now - held.hold_t) / 1.0, 1.0)
                held.hch = p
                if p >= 1.0:
                    held.mode = "move"
                    held.ox, held.oy = held.x - cur.x, held.y - cur.y
                    held.hold_x, held.hold_y, held.hold_t = cur.x, cur.y, now
                    held.hch = 0.6
        elif held.mode == "rotate" and held.grabbed_by[0] == i:
            rot = 0.012 * 960
            dyaw = (cur.x - held.grab_x0) * rot / 80
            dpitch = (cur.y - held.grab_y0) * rot / 80
            if held.kind == "model":
                held.ry = held.rot0y + dyaw
                held.rx = max(-1.2, min(1.2, held.rot0x + dpitch))
                pk, vx, _vy, _ls = peak_velocity(cur.history)
                held.rvy = vx * 0.8
            else:
                held.ry = max(-1.2, min(1.2, held.rot0y + dyaw))
                held.rx = max(-1.2, min(1.2, held.rot0x + dpitch))
        elif held.mode == "move" and held.grabbed_by[0] == i:
            held.x = cur.x + held.ox
            held.y = cur.y + held.oy
            held.clamp()
            drift = _dist((cur.x, cur.y), (held.hold_x, held.hold_y))
            if drift > 0.045:
                held.hold_x, held.hold_y, held.hold_t = cur.x, cur.y, now
                held.hch = 0.0
            else:
                mp = min((now - held.hold_t) / 1.0, 1.0)
                held.hch = mp
                if mp >= 1.0:
                    held.mode = "rotate"
                    held.rot0x, held.rot0y = held.rx, held.ry
                    held.grab_x0, held.grab_y0 = cur.x, cur.y
                    held.hch = 0.6
                    self.toast("3D rotate — move your hand to turn it")

    def _tick_clap(self, now: float) -> bool:
        curs = list(self._cursors.values())
        cool = now > self._clap_until and now > self._clear_until
        if len(curs) != 2:
            if self._clap_hist:
                last = self._clap_hist[-1]
                if (
                    now - last["t"] < 0.20
                    and last["q"]
                    and last["w"] < 0.16
                    and any(s["w"] > CLAP_APART for s in self._clap_hist)
                    and cool
                ):
                    self.reset()
                    self._clap_until = now + 1.5
                    self._clap_hist = []
                    return True
                self._clap_hist = []
            return False
        a, b = curs[0], curs[1]
        recent = a.pinched or b.pinched or now - max(a.last_pinch_t, b.last_pinch_t) < 0.80
        if any(c.grabbed_by for c in self.cards) or recent:
            self._clap_hist = []
            return False
        wrist_d = _dist(a.wrist, b.wrist)
        mcp_d = _dist(a.mcp, b.mcp)
        both_open = (
            (a.soft_open or now - a.last_soft_t < 0.25)
            and (b.soft_open or now - b.last_soft_t < 0.25)
        )
        both_up = a.hand_up > 0.85 and b.hand_up > 0.85
        self._clap_hist.append({"t": now, "w": wrist_d, "q": 1 if both_open and both_up else 0})
        self._clap_hist = [s for s in self._clap_hist if now - s["t"] <= 0.90]
        was_apart = any(now - s["t"] <= 0.80 and s["w"] > CLAP_APART for s in self._clap_hist)
        if wrist_d < CLAP_WRIST and mcp_d < CLAP_MCP and both_up and both_open and was_apart and cool:
            self.reset()
            self._clap_until = now + 1.5
            self._clap_hist = []
            return True
        return False

    def _tick_physics(self, dt: float, now: float) -> None:
        live = []
        for c in self.cards:
            if c.anim:
                a = c.anim
                a["t"] = a.get("t", 0.0) + dt
                k = a.get("k")
                if k == "in":
                    p = min(max((a["t"] - a.get("d", 0.0)) / 0.5, 0.0), 1.0)
                    c.in_s = 0.5 + 0.5 * p
                    if p >= 1:
                        c.anim = None
                        c.in_s = 1.0
                elif k == "out":
                    p = min(a["t"] / 0.35, 1.0)
                    c.in_s = 1 - 0.7 * p * p
                    if p >= 1:
                        continue
                elif k == "yank":
                    if a["t"] < 0.18:
                        c.jx = (np.random.random() - 0.5) * 0.02
                        c.jy = (np.random.random() - 0.5) * 0.02
                    else:
                        c.x -= 2.8 * dt
                        if c.x < -0.4:
                            continue
                elif k == "hover":
                    c.hch = 0.45 + 0.35 * math.sin(a["t"] * 10)
                    if a["t"] > 2.2:
                        c.anim = None
                        c.hch = 0.0
                elif k == "present":
                    p = min(a["t"] / 0.45, 1.0)
                    e = 1 - (1 - p) ** 3
                    c.x = a["x0"] + (0.50 - a["x0"]) * e
                    c.y = a["y0"] + (0.44 - a["y0"]) * e
                    c.scale = a["s0"] + (a["s1"] - a["s0"]) * e
                    if p >= 1:
                        c.anim = None
                elif k == "pull":
                    p = min(a["t"] / 0.32, 1.0)
                    e = p * p
                    cc = self._cursors.get(a["ci"])
                    tx, ty = (cc.x, cc.y) if cc else (a["x0"], a["y0"])
                    c.x = a["x0"] + (tx - a["x0"]) * e
                    c.y = a["y0"] + (ty - a["y0"]) * e
                    if p >= 1:
                        c.anim = None
                        if cc:
                            c.grabbed_by = [a["ci"]]
                            c.grabbed = True
                            c.pull_t = now
                            c.mode = "move"
                            c.ox = c.oy = 0.0
                            cc.grab_t = now
                            c.hch = 0.0
            if c.kind == "model" and not c.grabbed_by and (not c.anim or c.anim.get("k") not in ("yank", "out")):
                c.rvy *= max(0.0, 1.0 - 4.0 * dt)
                c.ry += (c.rvy + 0.55) * dt
            if c.flying:
                c.x += c.vx * dt
                c.y += c.vy * dt
                c.vx *= 0.98
                c.vy *= 0.98
                if c.x < -0.25 or c.x > 1.25 or c.y < -0.25 or c.y > 1.25:
                    continue
            elif not c.grabbed_by and not (c.anim and c.anim.get("k") in ("yank", "out", "pull", "present")):
                c.x += c.vx * dt
                c.y += c.vy * dt
                c.vx *= FRICTION
                c.vy *= FRICTION
                if abs(c.vx) < 0.02:
                    c.vx = 0.0
                if abs(c.vy) < 0.02:
                    c.vy = 0.0
                c.clamp()
            if self._presented == c.uid and c.grabbed_by:
                self._end_present()
            live.append(c)
        self.cards = live


def py_on_bar(card: Card, py: float) -> bool:
    top = card.y - card.h * card.scale / 2
    return py <= top + 0.04 * card.scale


def _camera_index() -> int:
    try:
        from memory.config_manager import load_api_keys
        return int(load_api_keys().get("camera_index", 0))
    except Exception:
        return 0


def _backend():
    return cv2.CAP_DSHOW if __import__("platform").system() == "Windows" else cv2.CAP_ANY


def _open_capture(idx: int | None = None):
    if not _CV2:
        raise RuntimeError("OpenCV is not installed.")
    if idx is None:
        idx = _camera_index()
    cap = cv2.VideoCapture(idx, _backend())
    if not cap.isOpened():
        for alt in range(8):
            cap = cv2.VideoCapture(alt, _backend())
            if cap.isOpened():
                idx = alt
                break
    if not cap.isOpened():
        raise RuntimeError("Could not open the webcam.")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 360)
    cap.set(cv2.CAP_PROP_FPS, 24)
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass
    return cap, idx


def _make_landmarker(path: Path):
    import mediapipe as mp
    from mediapipe.tasks.python import vision
    from mediapipe.tasks.python.core import base_options as base_options_mod

    options = vision.HandLandmarkerOptions(
        base_options=base_options_mod.BaseOptions(model_asset_path=str(path)),
        num_hands=2,
        min_hand_detection_confidence=0.70,
        min_hand_presence_confidence=0.50,
        min_tracking_confidence=0.5,
        running_mode=vision.RunningMode.VIDEO,
    )
    return vision.HandLandmarker.create_from_options(options), mp


def _hands_from_result(result) -> list:
    out = []
    if not result or not result.hand_landmarks:
        return out
    for hand in result.hand_landmarks:
        out.append([(lm.x, lm.y, getattr(lm, "z", 0.0) or 0.0) for lm in hand])
    return out


def _wrap(text: str, width: int = 22) -> list[str]:
    words = (text or "").split()
    if not words:
        return []
    lines, cur = [], words[0]
    for w in words[1:]:
        if len(cur) + 1 + len(w) <= width:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines[:6]


_IMG_CACHE: dict[str, tuple[float, np.ndarray]] = {}
_BLIT_CACHE: dict[tuple, np.ndarray] = {}
_TRACK_WIDTH = 320
_DISPLAY_WIDTH = 640
_TARGET_DT = 1.0 / 24.0


def _load_image(path: str):
    if not path or not _CV2:
        return None
    try:
        p = Path(path)
        if not p.is_file():
            return None
        mtime = p.stat().st_mtime
        hit = _IMG_CACHE.get(str(p))
        if hit and hit[0] == mtime:
            return hit[1]
        raw = np.fromfile(str(p), dtype=np.uint8)
        img = cv2.imdecode(raw, cv2.IMREAD_UNCHANGED)
        if img is None or img.size == 0:
            return None
        _IMG_CACHE[str(p)] = (mtime, img)
        return img
    except Exception:
        return None


def _blit_image(frame, img, x1: int, y1: int, x2: int, y2: int) -> bool:
    fh, fw = frame.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(fw, x2), min(fh, y2)
    rw, rh = x2 - x1, y2 - y1
    if rw < 4 or rh < 4:
        return False
    key = (id(img), rw, rh)
    scaled = _BLIT_CACHE.get(key)
    if scaled is None or scaled.shape[1] != rw or scaled.shape[0] != rh:
        interp = cv2.INTER_AREA if (img.shape[1] > rw or img.shape[0] > rh) else cv2.INTER_LINEAR
        scaled = cv2.resize(img, (rw, rh), interpolation=interp)
        if len(_BLIT_CACHE) > 24:
            _BLIT_CACHE.clear()
        _BLIT_CACHE[key] = scaled
    roi = frame[y1:y2, x1:x2]
    if scaled.ndim == 2:
        scaled = cv2.cvtColor(scaled, cv2.COLOR_GRAY2BGR)
    if scaled.shape[2] == 4:
        alpha = scaled[:, :, 3:4].astype(np.float32) / 255.0
        bgr = scaled[:, :, :3].astype(np.float32)
        roi[:] = (bgr * alpha + roi.astype(np.float32) * (1.0 - alpha)).astype(np.uint8)
    else:
        roi[:] = scaled[:, :, :3]
    return True


def _draw_model(frame, card: Card, dim: bool = False) -> None:
    from actions.hands_models import load_model, project_model, demo_engine
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = _card_rect(card, w, h)
    cx = (x1 + x2) // 2
    cy = (y1 + y2) // 2
    radius = max(28.0, min(x2 - x1, y2 - y1) * 0.48)
    try:
        model = load_model(card.model)
    except Exception:
        model = demo_engine()
    try:
        projected = project_model(model, card.rx, card.ry, card.ex, cx, cy, radius)
    except Exception:
        projected = []
    wire = (0, 80, 90) if dim else ((0, 255, 210) if card.holo else (40, 180, 255))
    fill = (0, 40, 50) if dim else ((0, 70, 80) if card.holo else (20, 90, 140))
    if not card.holo and not dim:
        for xy, _edges, faces in projected:
            if len(faces) == 0 or len(xy) < 3:
                continue
            step = max(1, len(faces) // 400)
            for tri in faces[::step]:
                if np.any(tri < 0) or np.any(tri >= len(xy)):
                    continue
                try:
                    cv2.fillConvexPoly(frame, xy[tri], fill, cv2.LINE_AA)
                except Exception:
                    continue
    for xy, edges, _faces in projected:
        n = len(xy)
        if len(edges):
            for a, b in edges:
                ia, ib = int(a), int(b)
                if 0 <= ia < n and 0 <= ib < n:
                    cv2.line(frame, tuple(xy[ia]), tuple(xy[ib]), wire, 1, cv2.LINE_AA)
        elif n:
            for p in xy[:: max(1, n // 400)]:
                cv2.circle(frame, (int(p[0]), int(p[1])), 1, wire, -1, cv2.LINE_AA)
    if card.holo and not dim:
        scan = int(cy - radius + (time.monotonic() % 1.6) / 1.6 * radius * 2)
        cv2.line(frame, (int(cx - radius), scan), (int(cx + radius), scan), (0, 255, 180), 1, cv2.LINE_AA)
    if card.ex > 0.05:
        cv2.putText(frame, f"EX {int(card.ex * 100)}%", (x1, y2 + 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, wire, 1, cv2.LINE_AA)
    cv2.putText(frame, card.title[:18], (x1, y1 + 16),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 212, 255), 1, cv2.LINE_AA)


def _draw_skeleton(frame, hands) -> None:
    h, w = frame.shape[:2]
    bone, joint = (0, 200, 230), (180, 245, 255)
    thumb_c, index_c = (0, 165, 255), (0, 255, 200)
    for hand in hands:
        if len(hand) < 21:
            continue
        pts = [(int(p[0] * w), int(p[1] * h)) for p in hand]
        for a, b in _CONNECTIONS:
            cv2.line(frame, pts[a], pts[b], bone, 2)
        for i, p in enumerate(pts):
            if i == _THUMB:
                cv2.circle(frame, p, 7, thumb_c, -1)
            elif i == _INDEX:
                cv2.circle(frame, p, 7, index_c, -1)
            elif i in (12, 16, 20):
                cv2.circle(frame, p, 4, joint, -1)
            else:
                cv2.circle(frame, p, 3, bone, -1)
        cv2.line(frame, pts[_THUMB], pts[_INDEX], (0, 255, 180), 2)


def _card_rect(card: Card, fw: int, fh: int):
    sc = card.scale * (card.in_s or 1.0)
    hw = card.w * sc / 2
    hh = card.h * sc / 2
    # Glass cards get a little 2.5D squash. Models already rotate inside
    # the projector — using yaw here shrinks them to nothing as they spin.
    if card.kind in ("model", "ring"):
        kx = ky = 1.0
    else:
        kx = math.cos(card.ry) if abs(card.ry) > 0.02 else 1.0
        ky = math.cos(card.rx) if abs(card.rx) > 0.02 else 1.0
        kx = max(0.35, abs(kx))
        ky = max(0.35, abs(ky))
    cx = card.x + card.jx
    cy = card.y + card.jy
    x1 = int((cx - hw * kx) * fw)
    y1 = int((cy - hh * ky) * fh)
    x2 = int((cx + hw * kx) * fw)
    y2 = int((cy + hh * ky) * fh)
    return x1, y1, x2, y2


def draw_board(frame, board: HandsBoard, hands, hint: dict, banner: str = "") -> None:
    h, w = frame.shape[:2]
    cv2.convertScaleAbs(frame, dst=frame, alpha=0.78, beta=4)
    cards = board.snapshot()
    spotlight = any(c.presented for c in cards)

    for card in cards:
        if card.kind == "ring":
            # 3D FRIDAY orb is painted by HandsBoardWidget with the HUD renderer.
            continue
        if card.kind == "model":
            _draw_model(frame, card, dim=spotlight and not card.presented)
            continue
        x1, y1, x2, y2 = _card_rect(card, w, h)
        dim = spotlight and not card.presented
        fill = (0, 40, 55) if card.grabbed else (0, 22, 32)
        edge = (0, 255, 220) if card.presented or card.hch > 0.5 else (
            (0, 212, 255) if card.grabbed else (0, 122, 153)
        )
        if dim:
            fill, edge = (0, 10, 14), (0, 50, 60)
        cv2.rectangle(frame, (x1, y1), (x2, y2), fill, -1)
        painted = False
        if card.image and not dim:
            img = _load_image(card.image)
            if img is not None:
                painted = _blit_image(frame, img, x1 + 2, y1 + 26, x2 - 2, y2 - 2)
        cv2.rectangle(frame, (x1, y1), (x2, y2), edge, 2 if not card.presented else 3)
        cv2.putText(
            frame, card.title[:22], (x1 + 10, y1 + 20),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 212, 255), 1, cv2.LINE_AA,
        )
        if not painted and not dim:
            body = card.body
            if card.open:
                y0 = y1 + 48 - int(card.scroll_y * h)
            else:
                y0 = y1 + 48
            for i, line in enumerate(_wrap(body)):
                cv2.putText(
                    frame, line, (x1 + 10, y0 + i * 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (180, 240, 255), 1, cv2.LINE_AA,
                )

    _draw_skeleton(frame, hands)
    if hint.get("pinch"):
        px, py = hint["pinch"]
        cv2.circle(frame, (int(px * w), int(py * h)), 16, (0, 212, 255), 2)
    if hint.get("claw"):
        cv2.putText(frame, "CLAW", (w - 90, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 165, 255), 2, cv2.LINE_AA)

    label = board.banner()
    if hint.get("clapping"):
        label = "CLAP — ring center stage"
    cv2.putText(frame, label, (16, h - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (90, 180, 200), 1, cv2.LINE_AA)
    text = banner or hint.get("debug") or ""
    if text:
        cv2.putText(frame, text, (16, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 212, 255), 1, cv2.LINE_AA)


def run_loop(
    board: HandsBoard,
    stop: threading.Event,
    on_frame: Callable[[bytes, int, int], None],
) -> str:
    if not _CV2:
        return "OpenCV is not installed."
    try:
        path = ensure_model()
    except Exception as e:
        return f"Couldn't load the hand model. ({e})"
    try:
        landmarker, mp = _make_landmarker(path)
    except Exception as e:
        return f"Couldn't start hand tracking. ({e})"
    try:
        cap, idx = _open_capture(board.camera_index)
        board.camera_index = idx
    except Exception as e:
        try:
            landmarker.close()
        except Exception:
            pass
        return str(e)

    t0 = time.monotonic()
    last = t0
    ts_ms = 0
    track_err = ""
    try:
        while not stop.is_set():
            loop_t = time.monotonic()
            if board.consume_cam_cycle():
                cap.release()
                nxt = (board.camera_index + 1) % 8
                try:
                    cap, idx = _open_capture(nxt)
                    board.camera_index = idx
                    board.toast(f"camera {idx}")
                    t0 = time.monotonic()
                    ts_ms = 0
                except Exception as e:
                    board.toast(f"camera failed ({e})")
                    cap, idx = _open_capture(board.camera_index)
            ok, frame = cap.read()
            if not ok or frame is None:
                if stop.wait(0.02):
                    break
                continue
            frame = cv2.flip(frame, 1)
            fh, fw = frame.shape[:2]
            if fw > _TRACK_WIDTH:
                tw = _TRACK_WIDTH
                th = max(1, int(fh * tw / fw))
                small = cv2.resize(frame, (tw, th), interpolation=cv2.INTER_AREA)
            else:
                small = frame
            rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
            now = time.monotonic()
            dt = min(0.08, max(0.012, now - last))
            last = now
            ts_ms = max(ts_ms + 1, int((now - t0) * 1000) + 1)
            try:
                mp_image = mp.Image(
                    image_format=mp.ImageFormat.SRGB,
                    data=np.ascontiguousarray(rgb),
                )
                result = landmarker.detect_for_video(mp_image, ts_ms)
                hands = _hands_from_result(result)
            except Exception as e:
                hands = []
                ts_ms += 1
                if not track_err:
                    track_err = str(e)
                    print(f"[Hands] track: {e}")
            hint = board.tick(hands, dt)
            draw_board(frame, board, hands, hint)
            show = frame
            sh, sw = show.shape[:2]
            if sw > _DISPLAY_WIDTH:
                nh = max(1, int(sh * _DISPLAY_WIDTH / sw))
                show = cv2.resize(show, (_DISPLAY_WIDTH, nh), interpolation=cv2.INTER_AREA)
            rgb_out = cv2.cvtColor(show, cv2.COLOR_BGR2RGB)
            oh, ow = rgb_out.shape[:2]
            on_frame(rgb_out.tobytes(), ow, oh)
            spent = time.monotonic() - loop_t
            if spent < _TARGET_DT and stop.wait(_TARGET_DT - spent):
                break
    finally:
        cap.release()
        try:
            landmarker.close()
        except Exception:
            pass
    return ""
