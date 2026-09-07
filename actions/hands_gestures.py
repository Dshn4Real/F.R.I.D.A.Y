"""Barehands-style pose gates, measured from MediaPipe's 21 landmarks.

Thresholds are the same ratios the fullstack-agent board uses: gap/span
for pinch, OK-sign contrast so a fist is not a pinch, prayer clap, and
the claw (open flash → hooked C → 2s strain → snap).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# Landmark indices
WRIST, THUMB, INDEX, MIDDLE_MCP = 0, 4, 8, 9
PINKY_MCP = 17

# Pinch (gap / palm-span)
PINCH_ON_FRONT = 0.32
PINCH_ON_PROFILE = 0.38
PINCH_ON = PINCH_ON_FRONT
PINCH_OFF = 0.55
PINCH_OFF_FAST = 0.70
PINCH_ON_FRAMES = 2
PINCH_OFF_FRAMES = 2
GHOST_SPEED = 0.94          # ~900 px/s on a 960-wide frame
FAST_SPEED = 0.83           # ~800 px/s
HEAL_SPEED = 0.52           # ~500 px/s
FLICK_PEAK = 1.35           # ~1300 px/s
FLICK_FOLLOW = 0.40

# Clap (normalized 0–1 board)
CLAP_APART = 0.18
CLAP_WRIST = 0.11
CLAP_MCP = 0.09
CLAP_UP = 0.85

# Claw mouth (gap/span) and finger curls
CLAW_MOUTH_IN = 0.80
CLAW_MOUTH_HOLD = 0.68
SNAP_ABS = 0.34
SNAP_FAST = 0.48
SNAP_DROP = 0.22


def _xyz(p) -> tuple[float, float, float]:
    if len(p) >= 3:
        return float(p[0]), float(p[1]), float(p[2])
    return float(p[0]), float(p[1]), 0.0


def _d2(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _d3(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2])


def pinch_point(hand) -> tuple[float, float]:
    t, i = _xyz(hand[THUMB]), _xyz(hand[INDEX])
    return ((t[0] + i[0]) / 2, (t[1] + i[1]) / 2)


def pinch_ratio(hand) -> float:
    if len(hand) < 10:
        return 1.0
    w, m = _xyz(hand[WRIST]), _xyz(hand[MIDDLE_MCP])
    span = _d2(w, m) or 1e-6
    return _d2(_xyz(hand[THUMB]), _xyz(hand[INDEX])) / span


def palm_center(hand) -> tuple[float, float]:
    p = _xyz(hand[MIDDLE_MCP] if len(hand) > MIDDLE_MCP else hand[0])
    return p[0], p[1]


@dataclass
class HandMetrics:
    pts: list
    span: float
    ratio: float
    aspect: float
    cursor: tuple[float, float]
    wrist: tuple[float, float]
    mcp: tuple[float, float]
    hand_up: float
    f8: float
    back_mean: float
    t_rel: float
    ok_sign: bool
    ext_fingers: int
    palm_open: bool
    soft_open: bool
    claw: bool
    claw_hold: bool
    c8: float = 0.0
    c12: float = 0.0
    c16: float = 0.0
    c20: float = 0.0
    garbage: bool = False


def _finger_ratio(pts, tip: int, mcp: int) -> float:
    w = _xyz(pts[WRIST])
    md = _d2(_xyz(pts[mcp]), w)
    return (_d2(_xyz(pts[tip]), w) / md) if md > 0 else 9.0


def _seg(pts, a: int, b: int):
    pa, pb = _xyz(pts[a]), _xyz(pts[b])
    d = (pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2])
    n = math.hypot(*d) or 1.0
    return (d[0] / n, d[1] / n, d[2] / n)


def _curl(pts, mcp: int, pip: int, dip: int, tip: int) -> float:
    p, d = _seg(pts, mcp, pip), _seg(pts, dip, tip)
    return p[0] * d[0] + p[1] * d[1] + p[2] * d[2]


def _hook(pts, tip: int, pip: int) -> float:
    w = _xyz(pts[WRIST])
    pd = _d3(_xyz(pts[pip]), w)
    return (_d3(_xyz(pts[tip]), w) / pd) if pd > 0 else 9.0


def measure(hand) -> HandMetrics | None:
    if len(hand) < 21:
        return None
    pts = [_xyz(p) for p in hand]
    w, m = pts[WRIST], pts[MIDDLE_MCP]
    span = _d2(w, m) or 1e-6
    ratio = _d2(pts[THUMB], pts[INDEX]) / span
    palm_w = _d2(pts[5], pts[PINKY_MCP]) or 1e-6
    aspect = span / palm_w
    f8 = _finger_ratio(pts, 8, 5)
    back = (
        _finger_ratio(pts, 12, 9)
        + _finger_ratio(pts, 16, 13)
        + _finger_ratio(pts, 20, 17)
    ) / 3.0
    t_rel = _d2(pts[THUMB], pts[13]) / span
    ok_sign = (back - f8 > 0.18 and back > 1.30) or (aspect < 2.0 and t_rel > 0.95)
    ext = sum(
        1
        for tip, mcp in ((8, 5), (12, 9), (16, 13), (20, 17))
        if _d2(pts[tip], w) > 1.45 * _d2(pts[mcp], w)
    )
    c8 = _curl(pts, 5, 6, 7, 8)
    c12 = _curl(pts, 9, 10, 11, 12)
    c16 = _curl(pts, 13, 14, 15, 16)
    c20 = _curl(pts, 17, 18, 19, 20)
    h8, h12, h16 = _hook(pts, 8, 6), _hook(pts, 12, 10), _hook(pts, 16, 14)
    c_mean = (c8 + c12 + c16) / 3.0

    def claw_at(hold: bool) -> bool:
        mouth_lo = CLAW_MOUTH_HOLD if hold else CLAW_MOUTH_IN
        mouth_hi = 1.8 if hold else 1.45
        return (
            mouth_lo < ratio < mouth_hi
            and c8 < (0.8 if hold else 0.6)
            and c12 < (0.6 if hold else 0.35)
            and c16 < (0.75 if hold else 0.55)
            and c_mean < (0.55 if hold else 0.30)
            and c20 > (-0.35 if hold else 0.1)
            and aspect > (0.85 if hold else 1.05)
            and h8 < 1.6
            and h12 < 1.6
            and h16 < 1.6
        )

    return HandMetrics(
        pts=pts,
        span=span,
        ratio=ratio,
        aspect=aspect,
        cursor=pinch_point(pts),
        wrist=(w[0], w[1]),
        mcp=(m[0], m[1]),
        hand_up=(w[1] - m[1]) / span,
        f8=f8,
        back_mean=back,
        t_rel=t_rel,
        ok_sign=ok_sign,
        ext_fingers=ext,
        palm_open=ext >= 4 and ratio > 0.8,
        soft_open=ext >= 3 and ratio > 0.7,
        claw=claw_at(False),
        claw_hold=claw_at(True),
        c8=c8,
        c12=c12,
        c16=c16,
        c20=c20,
        garbage=aspect > 6,
    )


def speed(history: list, now: float) -> float:
    if len(history) < 2:
        return 0.0
    a, b = history[0], history[-1]
    dt = (b[2] - a[2]) if len(b) > 2 else 0.0
    if dt <= 0:
        dt = now - a[2] if len(a) > 2 else 0.0
    if dt <= 0:
        return 0.0
    return math.hypot(b[0] - a[0], b[1] - a[1]) / dt


def peak_velocity(history: list) -> tuple[float, float, float, float]:
    """Peak and last-instant speed over ~200ms of cursor history."""
    if len(history) < 2:
        return 0.0, 0.0, 0.0, 0.0
    t_end = history[-1][2]
    vx = vy = pk = 0.0
    for k in range(len(history) - 1):
        a, b = history[k], history[min(k + 2, len(history) - 1)]
        if a[2] < t_end - 0.22 or b[2] <= a[2]:
            continue
        d = b[2] - a[2]
        x2, y2 = (b[0] - a[0]) / d, (b[1] - a[1]) / d
        s = math.hypot(x2, y2)
        if s > pk:
            pk, vx, vy = s, x2, y2
    la, lb = history[max(0, len(history) - 3)], history[-1]
    ldt = lb[2] - la[2]
    last = math.hypot(lb[0] - la[0], lb[1] - la[1]) / ldt if ldt > 0 else 0.0
    return pk, vx, vy, last


@dataclass
class Cursor:
    x: float = 0.5
    y: float = 0.5
    pinched: bool = False
    history: list = field(default_factory=list)
    ok_ema: float = 0.0
    ok_prev: bool = False
    open_prev: bool = False
    bad_run: int = 0
    ghost: bool = False
    prob_kill: bool = False
    pinch_t: float = 0.0
    last_pinch_t: float = 0.0
    last_open_t: float = 0.0
    last_soft_t: float = 0.0
    grab_t: float = 0.0
    wrist: tuple[float, float] = (0.5, 0.5)
    mcp: tuple[float, float] = (0.5, 0.5)
    hand_up: float = 0.0
    palm_open: bool = False
    soft_open: bool = False
    ratio: float = 1.0
    fp_ph: int = 0
    fp_pose: int = 0
    fp_tid: int | None = None
    fp_lit: float = 0.0
    fp_lost: float = 0.0
    fp_ready: bool = False
    rh: list = field(default_factory=list)
    dbg: str = ""
    scrub: dict | None = None


def decide_pinch(cur: Cursor, m: HandMetrics, holding: bool, now: float) -> bool:
    """OK-sign pinch with speed-aware release. Same gates as barehands."""
    was = cur.pinched
    if m.garbage:
        cur.prob_kill = True
        return False
    cur.ok_ema = 0.70 * cur.ok_ema + 0.30 * (1.0 if m.ok_sign else 0.0)
    ok_now = m.ok_sign and cur.ok_prev
    cur.ok_prev = m.ok_sign
    hspd = speed(cur.history, now)
    rel_bar = PINCH_OFF_FAST if hspd > FAST_SPEED else PINCH_OFF
    open_read = m.ratio >= rel_bar
    rel_ok = (open_read and cur.open_prev) if hspd > FAST_SPEED else open_read
    cur.open_prev = open_read
    ceiling = PINCH_ON_PROFILE if m.aspect < 2.0 else PINCH_ON_FRONT
    pinched = (
        (not rel_ok)
        if was
        else (m.ratio < ceiling and (ok_now or cur.ok_ema > 0.55 or holding))
    )
    if pinched and not was:
        cur.bad_run = 0
        cur.prob_kill = False
    elif pinched and was and now - cur.pinch_t < 0.40 and hspd < 0.62 and not holding:
        cur.bad_run = 0 if m.ok_sign else cur.bad_run + 1
        if cur.bad_run >= 4:
            pinched = False
            cur.prob_kill = True
    else:
        cur.bad_run = 0
    return pinched


def claw_snap(cur: Cursor, ratio: float, now: float) -> bool:
    cur.rh.append((now, ratio))
    cur.rh = [r for r in cur.rh if now - r[0] <= 0.40]
    peak = max((r[1] for r in cur.rh if now - r[0] <= 0.28), default=0.0)
    return ratio < SNAP_ABS or (ratio < SNAP_FAST and peak - ratio > SNAP_DROP)
