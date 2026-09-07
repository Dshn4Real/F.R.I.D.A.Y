from __future__ import annotations

import math
import os
import platform
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

if platform.system() == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}

from PyQt6.QtCore import (
    QEasingCurve, QMimeData, QObject, QPoint, QPointF, QRectF, QSize, Qt,
    QTimer, pyqtSignal, QEvent,
)
from PyQt6.QtGui import (
    QBrush, QColor, QConicalGradient, QDragEnterEvent, QDropEvent, QFont,
    QFontDatabase, QImage, QKeySequence, QLinearGradient, QPainter, QPainterPath,
    QPen, QPixmap, QPolygonF, QRadialGradient, QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QStackedWidget, QTextEdit, QVBoxLayout, QWidget, QProgressBar,
)


from core.secure_store import read_json, write_json


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR   = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"


def _read_full_config() -> dict:
    """Read api_keys.json config dict. Returns {} on any error."""
    try:
        data = read_json(API_FILE)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_full_config(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    write_json(API_FILE, data)


_DEFAULT_W, _DEFAULT_H = 980, 700
_MIN_W,     _MIN_H     = 820, 580
_LEFT_W  = 148
_RIGHT_W = 340

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"


class C:
    BG        = "#000000"
    PANEL     = "#0a0604"
    PANEL2    = "#100804"
    BORDER    = "#4a2208"
    BORDER_B  = "#8a3c0c"
    BORDER_A  = "#5c2a0c"
    PRI       = "#ff6600"
    PRI_DIM   = "#cc4a00"
    PRI_GHO   = "#2a0e00"
    ACC       = "#ff8a1a"
    ACC2      = "#ffcc00"
    GREEN     = "#00ff88"
    GREEN_D   = "#00aa55"
    RED       = "#ff3355"
    MUTED_C   = "#ff3366"
    SPEAK     = "#00d4ff"
    TEXT      = "#ffd4b0"
    TEXT_DIM  = "#c47a48"
    TEXT_MED  = "#e8a06a"
    WHITE     = "#ffe8d4"
    DARK      = "#080301"
    BAR_BG    = "#140801"


# Ana renge (accent) bağlı anahtarlar — durum renkleri (ACC, GREEN, RED…) sabit kalır
_HUE_LINKED = (
    "BG", "PANEL", "PANEL2", "BORDER", "BORDER_B", "BORDER_A",
    "PRI", "PRI_DIM", "PRI_GHO", "TEXT", "TEXT_DIM", "TEXT_MED",
    "WHITE", "DARK", "BAR_BG",
)
_PALETTE_DEFAULTS: dict[str, str] = {k: getattr(C, k) for k in _HUE_LINKED}

DEFAULT_UI_COLOR = _PALETTE_DEFAULTS["PRI"]


def apply_ui_accent(accent_hex: str) -> bool:
    """
    Seçilen accent rengine göre tüm turkuaz-ailesi paleti yeniden türetir
    (hue kaydırma — parlaklık/doygunluk oranları korunur, tasarım bozulmaz).
    Boyanan öğeler (HUD, dalga formu, metrikler) bir sonraki karede yeni
    rengi alır; stylesheet tabanlı paneller yeniden kurulduklarında alır.
    """
    import colorsys

    accent_hex = (accent_hex or "").strip().lower()
    if not (accent_hex.startswith("#") and len(accent_hex) == 7):
        return False
    try:
        int(accent_hex[1:], 16)
    except ValueError:
        return False

    def _hsv(h: str) -> tuple[float, float, float]:
        r = int(h[1:3], 16) / 255
        g = int(h[3:5], 16) / 255
        b = int(h[5:7], 16) / 255
        return colorsys.rgb_to_hsv(r, g, b)

    base_h            = _hsv(_PALETTE_DEFAULTS["PRI"])[0]
    acc_h, acc_s, _av = _hsv(accent_hex)
    dh   = acc_h - base_h
    grey = acc_s < 0.08   # griye yakın accent → tüm tema desaturize edilir

    for key, hex0 in _PALETTE_DEFAULTS.items():
        h, s, v = _hsv(hex0)
        if grey:
            s *= 0.15
        r, g, b = colorsys.hsv_to_rgb((h + dh) % 1.0, s, v)
        setattr(C, key, "#{:02x}{:02x}{:02x}".format(
            int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5)))
    return True


def current_palette() -> dict[str, str]:
    """C sınıfındaki accent'e bağlı renklerin anlık kopyası."""
    return {k: getattr(C, k) for k in _HUE_LINKED}


def retheme_all_widgets(old: dict[str, str], new: dict[str, str]) -> None:
    """
    CANLI tam tema değişimi. Uygulamadaki HER widget'ın stylesheet'inde eski
    palet renklerini yenileriyle değiştirir ve yeniden çizdirir. Böylece renk
    değişimi yalnızca boyanan öğelerde değil, panel/buton/kenarlık dahil tüm
    arayüzde ANINDA uygulanır — yeniden başlatma gerekmez.
    """
    mapping = {old[k].lower(): new[k].lower()
               for k in old if old[k].lower() != new.get(k, old[k]).lower()}
    if not mapping:
        return
    app = QApplication.instance()
    if app is None:
        return
    for w in app.allWidgets():
        try:
            ss = w.styleSheet()
            if ss:
                s2 = ss
                for o, n in mapping.items():
                    if o in s2:
                        s2 = s2.replace(o, n)
                if s2 != ss:
                    w.setStyleSheet(s2)
            w.update()
        except Exception:
            pass


def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h); c.setAlpha(a); return c


_GLASS_BG        = "rgba(18, 8, 2, 0.55)"
_GLASS_BG_MED    = "rgba(16, 6, 2, 0.62)"
_GLASS_BG_HEAVY  = "rgba(12, 5, 1, 0.75)"
_GLASS_BORDER    = "rgba(255, 102, 0, 0.28)"
_GLASS_BORDER_HI = "rgba(255, 102, 0, 0.52)"
_GLASS_HIGHLIGHT = "rgba(255, 200, 140, 0.10)"
_GLASS_INPUT     = "rgba(12, 4, 0, 0.45)"
_GLASS_RADIUS    = 12
_GLASS_OVERLAY   = "rgba(10, 4, 0, 0.62)"


def _glass_ss(radius: int = _GLASS_RADIUS, heavy: bool = False) -> str:
    bg = _GLASS_BG_HEAVY if heavy else _GLASS_BG
    return (f"background: {bg}; border: 1px solid {_GLASS_BORDER}; "
            f"border-top: 1px solid {_GLASS_BORDER_HI}; border-radius: {radius}px;")


def _glass_overlay_ss(radius: int = _GLASS_RADIUS, accent: str | None = None) -> str:
    border = accent or _GLASS_BORDER
    return (f"background: {_GLASS_OVERLAY}; border: 1px solid {border}; "
            f"border-radius: {radius}px;")


def _glass_input_ss() -> str:
    return (f"background: {_GLASS_INPUT}; color: {C.WHITE}; "
            f"border: 1px solid {_GLASS_BORDER}; border-radius: 8px;")


def _glass_btn_pri_ss() -> str:
    return (f"QPushButton {{ background: rgba(255, 102, 0, 0.16); color: {C.PRI}; "
            f"border: 1px solid {_GLASS_BORDER_HI}; border-radius: 8px; }}"
            f"QPushButton:hover {{ background: rgba(255, 102, 0, 0.28); }}")


def _glass_btn_dim_ss() -> str:
    return (f"QPushButton {{ background: {_GLASS_INPUT}; color: {C.TEXT_MED}; "
            f"border: 1px solid {_GLASS_BORDER}; border-radius: 8px; }}"
            f"QPushButton:hover {{ color: {C.PRI}; border-color: {_GLASS_BORDER_HI}; "
            f"background: rgba(255, 102, 0, 0.12); }}")


class GlassRoot(QWidget):
    """Dark HUD shell — original solid mission-control look."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAutoFillBackground(False)

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(0, 0, -1, -1)

        for i, a in enumerate((12, 20, 32), start=1):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(255, 102, 0, a)))
            p.drawRoundedRect(r.adjusted(-i, -i, i, i), 14 + i, 14 + i)

        grad = QLinearGradient(0, 0, 0, r.height())
        grad.setColorAt(0.0, QColor(12, 6, 2, 255))
        grad.setColorAt(0.45, QColor(6, 3, 1, 255))
        grad.setColorAt(1.0, QColor(0, 0, 0, 255))
        p.setBrush(QBrush(grad))
        p.setPen(QPen(QColor(255, 102, 0, 55), 1.2))
        p.drawRoundedRect(r, 12, 12)

        hi = QLinearGradient(0, r.top(), 0, r.top() + 56)
        hi.setColorAt(0.0, QColor(255, 255, 255, 24))
        hi.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(QBrush(hi))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(r.adjusted(8, 8, -9, -r.height() + 52), 10, 10)
        p.end()



def _norm3(v: tuple[float, float, float]) -> tuple[float, float, float]:
    l = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) or 1.0
    return (v[0] / l, v[1] / l, v[2] / l)


def _build_geodesic(subdiv: int = 2):
    """Icosphere verts + unique edges for the JARVIS wireframe core."""
    t = (1.0 + math.sqrt(5.0)) / 2.0
    verts = [
        _norm3(v) for v in (
            (-1,  t, 0), (1,  t, 0), (-1, -t, 0), (1, -t, 0),
            (0, -1,  t), (0,  1, t), (0, -1, -t), (0,  1, -t),
            ( t, 0, -1), (t,  0, 1), (-t,  0, -1), (-t, 0,  1),
        )
    ]
    faces = [
        (0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11),
        (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
        (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9),
        (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1),
    ]
    for _ in range(subdiv):
        mid: dict[tuple[int, int], int] = {}
        nxt: list[tuple[int, int, int]] = []

        def _mid(a: int, b: int) -> int:
            key = (a, b) if a < b else (b, a)
            if key not in mid:
                va, vb = verts[a], verts[b]
                mid[key] = len(verts)
                verts.append(_norm3((va[0] + vb[0], va[1] + vb[1], va[2] + vb[2])))
            return mid[key]

        for a, b, c in faces:
            ab, bc, ca = _mid(a, b), _mid(b, c), _mid(c, a)
            nxt.extend(((a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)))
        faces = nxt
    edges = set()
    for a, b, c in faces:
        for u, v in ((a, b), (b, c), (c, a)):
            edges.add((u, v) if u < v else (v, u))
    return verts, list(edges)


_GEO_VERTS, _GEO_EDGES = _build_geodesic(2)


def _rot3(v, ay: float, ax: float):
    x, y, z = v
    cy, sy = math.cos(ay), math.sin(ay)
    x, z = x * cy - z * sy, x * sy + z * cy
    cx, sx = math.cos(ax), math.sin(ax)
    y, z = y * cx - z * sx, y * sx + z * cx
    return x, y, z


_FRIDAY_PATH: QPainterPath | None = None


def _friday_text_path() -> QPainterPath:
    """Centered outline of F.R.I.D.A.Y — built once, then projected in 3D."""
    global _FRIDAY_PATH
    if _FRIDAY_PATH is None:
        font = QFont("Segoe UI", 56, QFont.Weight.Bold)
        font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 114)
        path = QPainterPath()
        path.addText(QPointF(0, 0), font, "F.R.I.D.A.Y")
        br = path.boundingRect()
        path.translate(-br.center().x(), -br.center().y())
        _FRIDAY_PATH = path
    return _FRIDAY_PATH


def _paint_friday_word(p: QPainter, cx: float, cy: float, scale: float,
                       yaw: float, pitch: float, accent: str) -> None:
    """Draw extruded 3D F.R.I.D.A.Y across the orb — readable, slight turn."""
    path = _friday_text_path()
    br = path.boundingRect()
    if br.width() < 1:
        return
    nx = 1.0 / br.width()
    ny = nx
    zf, zb = 0.18, -0.18

    def proj(x: float, y: float, z: float):
        px, py, pz = _rot3((x * nx * scale, y * ny * scale, z * scale * 0.55), yaw, pitch)
        persp = 1.0 / max(0.55, 1.28 - pz * 0.002)
        return QPointF(cx + px * persp, cy + py * persp), pz

    polys = path.toSubpathPolygons()

    p.setBrush(Qt.BrushStyle.NoBrush)
    for poly in polys:
        back = QPolygonF()
        for pt in poly:
            q, _ = proj(pt.x(), pt.y(), zb)
            back.append(q)
        p.setPen(QPen(qcol(accent, 70), 1.0))
        p.drawPolygon(back)

    for poly in polys:
        step = max(1, len(poly) // 28)
        for i, pt in enumerate(poly):
            if i % step:
                continue
            a, _ = proj(pt.x(), pt.y(), zf)
            b, _ = proj(pt.x(), pt.y(), zb)
            p.setPen(QPen(qcol(accent, 55), 1.0))
            p.drawLine(a, b)

    for poly in polys:
        front = QPolygonF()
        for pt in poly:
            q, _ = proj(pt.x(), pt.y(), zf)
            front.append(q)
        p.setPen(QPen(qcol(C.WHITE, 230), 2.0))
        p.setBrush(QBrush(qcol(accent, 35)))
        p.drawPolygon(front)
        p.setBrush(Qt.BrushStyle.NoBrush)


def paint_friday_core(p: QPainter, cx: float, cy: float, r: float,
                      glow: float = 1.0, accent: QColor | None = None) -> None:
    """Glowing triangle-in-circle — the FRIDAY mission-control mark."""
    ac = accent or qcol(C.PRI)
    g  = max(0.2, min(1.5, glow))
    for i in range(3, 0, -1):
        p.setPen(QPen(qcol(C.PRI, int(35 * g / i)), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        off = i * 3.5
        p.drawEllipse(QRectF(cx - r - off, cy - r - off, (r + off) * 2, (r + off) * 2))
    p.setPen(QPen(ac, 1.8))
    p.setBrush(QBrush(qcol(C.PRI_GHO, int(70 * g))))
    p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
    tri = QPainterPath()
    h   = r * 1.12
    tri.moveTo(cx, cy - h * 0.52)
    tri.lineTo(cx + h * 0.48, cy + h * 0.34)
    tri.lineTo(cx - h * 0.48, cy + h * 0.34)
    tri.closeSubpath()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(qcol(C.PRI, int(230 * g))))
    p.drawPath(tri)


class _LogoBadge(QWidget):
    """Small painted FRIDAY geodesic orb for the header."""

    def __init__(self, size: int = 30, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = float(self.width())
        paint_friday_orb(
            p, w / 2, w / 2, w,
            accent=C.PRI, amp=0.08, pulse=1.0,
            speaking=False, muted=False, state="LISTENING",
            tick=40, rings=[18.0], spin_y=0.55, spin_x=0.28,
            pulses=[], particles=None, blink=True,
            detail="lite", show_status=False,
        )
        p.end()


class StatusRing(QWidget):
    """Circular system-health gauge — mission-control style."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pct   = 98.0
        self._label = "OPTIMAL"
        self.setFixedHeight(88)

    def set_status(self, pct: float, label: str = "OPTIMAL") -> None:
        self._pct   = max(0.0, min(100.0, pct))
        self._label = label
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        p.fillRect(self.rect(), Qt.GlobalColor.transparent)

        cx, cy = W * 0.28, H / 2
        r  = min(W * 0.22, H * 0.38)
        pct = self._pct / 100.0

        p.setPen(QPen(qcol(C.BORDER, 120), 5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        arc_rect = QRectF(cx - r, cy - r, r * 2, r * 2)
        p.setPen(QPen(qcol(C.PRI), 5))
        p.drawArc(arc_rect, 90 * 16, int(-pct * 360 * 16))

        p.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        p.drawText(QRectF(cx - r, cy - 10, r * 2, 20),
                   Qt.AlignmentFlag.AlignCenter, f"{int(self._pct)}%")
        p.setFont(QFont("Segoe UI", 7))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(cx - r, cy + 6, r * 2, 14),
                   Qt.AlignmentFlag.AlignCenter, self._label)

        lx = W * 0.52
        rows = [
            ("Performance", "Excellent", C.GREEN),
            ("Thermal",     "Normal",    C.TEXT_MED),
            ("Power",       "Balanced",  C.ACC2),
            ("Security",    "Secure",    C.PRI),
        ]
        y = 14
        for name, val, col in rows:
            p.setFont(QFont("Segoe UI", 8))
            p.setPen(QPen(qcol(C.TEXT_DIM), 1))
            p.drawText(QRectF(lx, y, W - lx - 4, 14),
                       Qt.AlignmentFlag.AlignLeft, name)
            p.setPen(QPen(qcol(col), 1))
            p.drawText(QRectF(lx, y, W - lx - 8, 14),
                       Qt.AlignmentFlag.AlignRight, val)
            y += 17
        p.end()


class FridayPresenceOrb(QWidget):
    """Same 3D FRIDAY orb as the HUD — visible when the main window is minimized."""

    _SIZE = 148

    def __init__(self, main_window: "MainWindow"):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self._main       = main_window
        self._drag       = None
        self._tick       = 0
        self.state       = "LISTENING"
        self.speaking    = False
        self.muted       = False
        self._live_amp   = 0.0
        self._amp_disp   = 0.0
        self._pulse      = 0.0

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(self._SIZE, self._SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(16)

    def set_audio_level(self, level: float) -> None:
        try:
            lv = float(level)
        except (TypeError, ValueError):
            return
        lv = max(0.0, min(1.0, lv))
        if lv > self._live_amp:
            self._live_amp = lv

    def sync_state(self, state: str, speaking: bool, muted: bool) -> None:
        self.state    = state
        self.speaking = speaking
        self.muted    = muted

    def _step(self):
        self._tick += 1
        self._live_amp *= 0.86
        self._amp_disp += (self._live_amp - self._amp_disp) * 0.45
        self._pulse = 0.5 + 0.5 * math.sin(self._tick * 0.08)
        self.update()

    def show_orb(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        self.move(geo.right() - self.width() - 28, geo.bottom() - self.height() - 28)
        self.show()
        self.raise_()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._drag is not None:
            moved = (e.globalPosition().toPoint() - self._drag - self.frameGeometry().topLeft()).manhattanLength()
            self._drag = None
            if moved < 8:
                self._main.showNormal()
                self._main.raise_()
                self._main.activateWindow()
                self.hide()
        else:
            self._drag = None

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2
        hud = getattr(self._main, "hud", None)
        speaking = self.speaking or self.state == "SPEAKING"
        if self.muted:
            accent = C.MUTED_C
        elif speaking:
            accent = C.SPEAK
        elif self.state in ("THINKING", "PROCESSING"):
            accent = C.ACC2
        else:
            accent = C.PRI
        if hud is not None:
            amp = float(hud._amp_disp)
            pulse = float(hud._scale)
            tick = int(hud._tick)
            rings = list(hud._rings)
            spin_y = float(hud._spin_y)
            spin_x = float(hud._spin_x)
            pulses = list(hud._pulses)
            blink = bool(hud._blink)
        else:
            amp = float(self._amp_disp)
            pulse = 1.0 + amp * 0.03 + 0.02 * self._pulse
            tick = int(self._tick)
            rings = [(self._tick * 0.55) % 360]
            spin_y = self._tick * 0.012
            spin_x = 0.22 + self._tick * 0.004
            pulses = []
            blink = True
        paint_friday_orb(
            p, cx, cy, float(min(W, H)),
            accent=accent, amp=amp, pulse=pulse,
            speaking=speaking, muted=self.muted, state=self.state,
            tick=tick, rings=rings, spin_y=spin_y, spin_x=spin_x,
            pulses=pulses, particles=None, blink=blink,
            show_status=False,
        )
        p.end()



# ── Windows GPU via NVML DLL (no subprocess, no console window) ──────────────
_nvml_lib: object = None   # cached ctypes DLL
_nvml_ok:  object = None   # None=untested, True=works, False=unavailable


def _nvml_gpu_windows() -> float:
    """Return NVIDIA GPU utilisation % using nvml.dll directly — zero subprocess."""
    global _nvml_lib, _nvml_ok
    if _nvml_ok is False:
        return -1.0
    try:
        import ctypes

        class _Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        if _nvml_lib is None:
            for dll_name in ("nvml", r"C:\Windows\System32\nvml.dll"):
                try:
                    lib = ctypes.WinDLL(dll_name)
                    lib.nvmlInit_v2()
                    _nvml_lib = lib
                    break
                except Exception:
                    continue

        if _nvml_lib is None:
            import pynvml  # type: ignore
            pynvml.nvmlInit()
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            _nvml_ok = True
            return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)

        dev = ctypes.c_void_p()
        _nvml_lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
        util = _Util()
        _nvml_lib.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(util))
        _nvml_ok = True
        return float(util.gpu)
    except Exception:
        _nvml_ok = False
        return -1.0


class _SysMetrics:
    def __init__(self):
        self.cpu  = 0.0
        self.mem  = 0.0
        self.net  = 0.0   
        self.gpu  = -1.0  
        self.tmp  = -1.0  
        self._lock = threading.Lock()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._running = True
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(1.5)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        nc  = psutil.net_io_counters()
        now = time.time()
        dt  = now - self._last_net_t
        if dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            net  = (sent + recv) / (1024 * 1024)
        else:
            net = 0.0
        self._last_net   = nc
        self._last_net_t = now

        gpu = self._get_gpu()

        tmp = self._get_temp()

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net
            self.gpu = gpu
            self.tmp = tmp

    def _get_gpu(self) -> float:
        # pynvml — subprocess-free, works on all platforms if installed
        try:
            import pynvml  # type: ignore
            pynvml.nvmlInit()
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)
        except Exception:
            pass

        # Windows: nvml.dll via ctypes (already cached in _nvml_gpu_windows)
        if _OS == "Windows":
            return _nvml_gpu_windows()

        # Linux / macOS: libnvidia-ml shared lib via ctypes
        try:
            import ctypes
            _lib = "libnvidia-ml.so.1" if _OS == "Linux" else "libnvidia-ml.dylib"

            class _Util(ctypes.Structure):
                _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

            nv = ctypes.CDLL(_lib)
            nv.nvmlInit_v2()
            dev = ctypes.c_void_p()
            nv.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
            u = _Util()
            nv.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(u))
            return float(u.gpu)
        except Exception:
            pass

        return -1.0   # N/A — zero subprocess on all platforms

    def _get_temp(self) -> float:
        # psutil — works on Linux; occasionally Windows with driver support
        try:
            temps = psutil.sensors_temperatures()
            for name in ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                         "cpu-thermal", "zenpower", "it8688"]:
                if name in temps and temps[name]:
                    return temps[name][0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            pass

        # Windows: wmi module (pure Python COM, zero subprocess)
        if _OS == "Windows":
            try:
                import wmi  # type: ignore
                w = wmi.WMI(namespace="root/wmi")
                tz = w.MSAcpi_ThermalZoneTemperature()
                if tz:
                    return (tz[0].CurrentTemperature / 10.0) - 273.15
            except Exception:
                pass

        return -1.0   # N/A — zero subprocess on all platforms

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "gpu": self.gpu,
                "tmp": self.tmp,
            }


_metrics = _SysMetrics()


def paint_friday_orb(
    p: QPainter,
    cx: float,
    cy: float,
    fw: float,
    *,
    accent: str,
    amp: float,
    pulse: float,
    speaking: bool,
    muted: bool,
    state: str,
    tick: int,
    rings: list[float],
    spin_y: float,
    spin_x: float,
    pulses: list[float],
    particles: list | None = None,
    blink: bool = True,
    detail: str = "full",
    show_status: bool = True,
) -> None:
    """Shared 3D FRIDAY geodesic orb — HUD canvas and hands board both use this.

    detail='lite' skips the aura dots, tick ring and extruded word so the
    hands board can animate at camera rate without stalling the pinch loop.
    """
    ac = QColor(accent)
    speaking = bool(speaking)
    amp = max(0.0, min(1.0, float(amp)))
    pulse = float(pulse)

    lite = detail == "lite"
    aura_r = fw * 0.34 * pulse * (1.0 + amp * 0.08)
    turb = 0.06 + amp * 0.07 + (0.03 if speaking else 0.0)
    n_aura = 0 if lite else 160
    p.setPen(Qt.PenStyle.NoPen)
    for i in range(n_aura):
        a = (i / n_aura) * math.pi * 2 + tick * 0.012
        wave = (
            math.sin(a * 5 + tick * 0.07) * turb
            + math.sin(a * 11 - tick * 0.05) * turb * 0.45
        )
        rr = aura_r * (1.0 + wave)
        x = cx + math.cos(a) * rr
        y = cy + math.sin(a) * rr * 0.92
        fade = 70 + int(amp * 45) + (10 if speaking else 0)
        sz = 1.4 + amp * 0.8 + (0.3 if speaking else 0.0)
        p.setBrush(QBrush(qcol(accent, min(255, fade))))
        p.drawEllipse(QPointF(x, y), sz, sz)

    bloom = QRadialGradient(cx, cy, fw * 0.28 * pulse)
    bloom.setColorAt(0.0, qcol(accent, int(55 + amp * 70 + (25 if speaking else 0))))
    bloom.setColorAt(0.55, qcol(accent, int(18 + amp * 25)))
    bloom.setColorAt(1.0, QColor(0, 0, 0, 0))
    p.setBrush(QBrush(bloom))
    p.setPen(Qt.PenStyle.NoPen)
    br = fw * 0.30 * pulse
    p.drawEllipse(QRectF(cx - br, cy - br, br * 2, br * 2))

    if speaking:
        lim = max(1.0, fw * 0.74)
        for pr in pulses:
            a = max(0, int(90 * (1.0 - pr / lim)))
            p.setPen(QPen(qcol(accent, a), 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - pr, cy - pr, pr * 2, pr * 2))

    segs = 0 if lite else 52
    ring_r = fw * 0.24 * pulse
    ring0 = rings[0] if rings else 0.0
    p.setPen(Qt.PenStyle.NoPen)
    for i in range(segs):
        if i % 9 in (7, 8):
            continue
        ang = ring0 + i * (360.0 / segs)
        wob = 0.55 + 0.45 * math.sin(tick * 0.11 + i * 0.55)
        hgt = 5.0 + wob * 3.0 + amp * 6.0 + (2.5 if speaking else 0.0)
        alpha = 140 + int(amp * 90) + (20 if speaking else 0)
        p.save()
        p.translate(cx, cy)
        p.rotate(ang)
        p.setBrush(QBrush(qcol(accent, min(255, alpha))))
        p.drawRoundedRect(QRectF(ring_r, -1.3, hgt, 2.6), 1.0, 1.0)
        p.restore()

    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(qcol(accent, 90), 1))
    ir = fw * 0.215 * pulse
    p.drawEllipse(QRectF(cx - ir, cy - ir, ir * 2, ir * 2))

    core_r = fw * 0.20 * pulse * (1.0 + amp * 0.04)
    pts = [_rot3(v, spin_y, spin_x) for v in _GEO_VERTS]
    geo_edges = _GEO_EDGES[::3] if lite else _GEO_EDGES
    for a, b in geo_edges:
        xa, ya, za = pts[a]
        xb, yb, zb = pts[b]
        zmid = (za + zb) * 0.5
        if zmid < -0.15:
            continue
        depth = (zmid + 1.0) * 0.5
        alpha = int(40 + depth * (150 + amp * 80))
        if speaking:
            alpha = min(255, alpha + 30)
        p.setPen(QPen(qcol(C.WHITE if depth > 0.72 else accent, min(255, alpha)),
                      0.9 + depth * 0.6))
        p.drawLine(
            QPointF(cx + xa * core_r, cy + ya * core_r),
            QPointF(cx + xb * core_r, cy + yb * core_r),
        )

    hot = QRadialGradient(cx - core_r * 0.18, cy - core_r * 0.22, core_r * 1.15)
    hot.setColorAt(0.0, QColor(
        min(255, ac.red() + 90), min(255, ac.green() + 70),
        min(255, ac.blue() + 50), int(90 + amp * 80)))
    hot.setColorAt(0.45, qcol(accent, int(35 + amp * 40)))
    hot.setColorAt(1.0, QColor(0, 20, 30, 0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(hot))
    p.drawEllipse(QRectF(cx - core_r, cy - core_r, core_r * 2, core_r * 2))

    if not lite:
        yaw = math.sin(tick * 0.014) * 0.32
        pitch = 0.10 + math.sin(tick * 0.010) * 0.06
        _paint_friday_word(p, cx, cy, fw * 0.36 * pulse, yaw, pitch, accent)

    for pt in (particles or ()):
        a = max(0, min(255, int(pt[4] * 255)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(qcol(accent, a)))
        p.drawEllipse(QPointF(pt[0], pt[1]), 2.2, 2.2)

    if not show_status:
        return

    sy = cy + fw * 0.42
    if muted:
        txt, col = "⊘  MUTED", qcol(C.MUTED_C)
    elif speaking:
        txt, col = "●  SPEAKING", qcol(C.SPEAK)
    elif state == "THINKING":
        txt, col = f"{'◈' if blink else '◇'}  THINKING", qcol(C.ACC2)
    elif state == "PROCESSING":
        txt, col = f"{'▷' if blink else '▶'}  PROCESSING", qcol(C.ACC2)
    elif state == "LISTENING":
        txt, col = f"{'●' if blink else '○'}  LISTENING", qcol(C.GREEN)
    else:
        txt, col = f"{'●' if blink else '○'}  {state}", qcol(C.PRI)
    p.setPen(QPen(col, 1))
    fs = max(7, min(11, int(fw * 0.038)))
    p.setFont(QFont("Courier New", fs, QFont.Weight.Bold))
    p.drawText(QRectF(cx - fw, sy, fw * 2, 26), Qt.AlignmentFlag.AlignCenter, txt)


class HudCanvas(QWidget):
    def __init__(self, face_path: str, assistant_name: str = "J.A.R.V.I.S", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.muted    = False
        self.speaking = False
        self.state    = "INITIALISING"
        self._assistant_name = assistant_name

        self._tick       = 0
        self._scale      = 1.0
        self._tgt_scale  = 1.0
        self._halo       = 55.0
        self._tgt_halo   = 55.0
        self._last_t     = time.time()
        self._scan       = 0.0
        self._scan2      = 180.0
        self._rings      = [0.0, 120.0, 240.0]
        self._spin_y     = 0.0
        self._spin_x     = 0.22
        self._pulses: list[float] = [0.0, 50.0, 100.0]
        self._blink      = True
        self._blink_tick = 0
        self._particles: list[list[float]] = []
        self._face_px: QPixmap | None = None
        self._load_face(face_path)

        # Live audio reactivity: _live_amp is written from the audio threads
        # (0.0–1.0), _amp_disp is the smoothed value the paint code reads.
        self._live_amp  = 0.0
        self._amp_disp  = 0.0
        self._base_scale = 1.0    # slow "breathing" target; amp is added per-frame
        self._base_halo  = 55.0

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(16)

    def set_audio_level(self, level: float) -> None:
        """Thread-safe entry point for the audio threads. Stores the louder of
        the incoming level and the current value so brief gaps between chunks
        don't make the waveform stutter; _step() decays it back down."""
        try:
            lv = float(level)
        except (TypeError, ValueError):
            return
        if lv < 0.0:
            lv = 0.0
        elif lv > 1.0:
            lv = 1.0
        if lv > self._live_amp:
            self._live_amp = lv

    def _load_face(self, path: str):
        try:
            from PIL import Image, ImageDraw
            import io
            img = Image.open(path).convert("RGBA")
            sz  = min(img.size)
            img = img.resize((sz, sz), Image.LANCZOS)
            mk  = Image.new("L", (sz, sz), 0)
            ImageDraw.Draw(mk).ellipse((2, 2, sz - 2, sz - 2), fill=255)
            img.putalpha(mk)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap(); px.loadFromData(buf.getvalue())
            self._face_px = px
        except Exception:
            self._face_px = None

    def _step(self):
        self._tick += 1
        now = time.time()

        # ── Live audio reactivity ────────────────────────────────────────────
        # Audio threads push peaks into _live_amp; decay it toward silence so
        # gaps between chunks fade out instead of freezing, then smooth it.
        self._live_amp *= 0.86
        self._amp_disp += (self._live_amp - self._amp_disp) * 0.45
        amp = self._amp_disp

        # Slow "breathing" base target (random shimmer), refreshed on a timer.
        if now - self._last_t > (0.12 if self.speaking else 0.5):
            if self.speaking:
                self._base_scale = 1.012
                self._base_halo  = 88.0
            elif self.muted:
                self._base_scale = random.uniform(0.998, 1.002)
                self._base_halo  = random.uniform(15, 28)
            else:
                self._base_scale = random.uniform(1.000, 1.004)
                self._base_halo  = random.uniform(48, 62)
            self._last_t = now

        # Every frame, the live audio level lifts the target on top of the base
        # — this is what makes the core visibly pulse to the actual voice.
        if self.muted:
            self._tgt_scale, self._tgt_halo = self._base_scale, self._base_halo
        elif self.speaking:
            self._tgt_scale = self._base_scale + amp * 0.055
            self._tgt_halo  = self._base_halo  + amp * 45.0
        else:
            self._tgt_scale = self._base_scale + amp * 0.025
            self._tgt_halo  = self._base_halo  + amp * 35.0

        sp = 0.20 if self.speaking else (0.16 if amp > 0.02 else 0.10)
        self._scale += (self._tgt_scale - self._scale) * sp
        self._halo  += (self._tgt_halo  - self._halo)  * sp

        # Rings + geodesic spin faster while speaking, reacting to loudness.
        boost  = 1.0 + amp * 1.6
        speeds = ([1.3, -0.9, 2.0] if self.speaking else [0.55, -0.35, 0.9])
        for i, spd in enumerate(speeds):
            self._rings[i] = (self._rings[i] + spd * boost) % 360

        self._spin_y += (0.028 if self.speaking else 0.012) * boost
        self._spin_x += (0.010 if self.speaking else 0.004) * boost
        self._scan  = (self._scan  + (3.0 if self.speaking else 1.3) * boost) % 360
        self._scan2 = (self._scan2 + (-2.0 if self.speaking else -0.75) * boost) % 360

        fw  = min(self.width(), self.height())
        lim = fw * 0.74
        spd = 4.2 if self.speaking else 2.0
        self._pulses = [r + spd for r in self._pulses if r + spd < lim]
        if len(self._pulses) < 2 and random.random() < (0.03 if self.speaking else 0.012):
            self._pulses.append(0.0)

        if self.speaking and random.random() < 0.12:
            cx, cy = self.width() / 2, self.height() / 2
            ang = random.uniform(0, 2 * math.pi)
            r_s = fw * 0.28
            self._particles.append([
                cx + math.cos(ang) * r_s, cy + math.sin(ang) * r_s,
                math.cos(ang) * random.uniform(0.9, 2.4),
                math.sin(ang) * random.uniform(0.9, 2.4) - 0.4, 1.0,
            ])
        self._particles = [
            [p[0]+p[2], p[1]+p[3], p[2]*0.97, p[3]*0.97, p[4]-0.028]
            for p in self._particles if p[4] > 0
        ]

        self._blink_tick += 1
        if self._blink_tick >= 38:
            self._blink = not self._blink
            self._blink_tick = 0
        if self.isVisible():
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        panel = QLinearGradient(0, 0, 0, H)
        panel.setColorAt(0.0, QColor(0, 8, 14, 220))
        panel.setColorAt(1.0, QColor(0, 4, 8, 240))
        p.setBrush(QBrush(panel))
        p.setPen(QPen(qcol(C.BORDER, 70), 1))
        p.drawRoundedRect(QRectF(6, 6, W - 12, H - 12), 12, 12)

        cx, cy = W / 2, H / 2 - 8
        fw  = min(W, H)
        amp = self._amp_disp
        pulse = self._scale
        if self.muted:
            accent = C.MUTED_C
        elif self.speaking or self.state == "SPEAKING":
            accent = C.SPEAK
        elif self.state in ("THINKING", "PROCESSING"):
            accent = C.ACC2
        else:
            accent = C.PRI

        paint_friday_orb(
            p, cx, cy, fw,
            accent=accent, amp=amp, pulse=pulse,
            speaking=self.speaking or self.state == "SPEAKING",
            muted=self.muted, state=self.state,
            tick=self._tick, rings=self._rings,
            spin_y=self._spin_y, spin_x=self._spin_x,
            pulses=self._pulses, particles=self._particles,
            blink=self._blink,
        )
        p.end()

class MetricBar(QWidget):

    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0       # 0–100
        self._text  = "--"
        self.setFixedHeight(38)
        self.setMinimumWidth(80)

    def set_value(self, pct: float, text: str):
        self._value = max(0.0, min(100.0, pct))
        self._text  = text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        p.setBrush(QBrush(qcol(C.PANEL2, 90)))
        p.setPen(QPen(qcol(C.BORDER_A, 70), 1))
        p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 8, 8)

        bar_h   = 4
        bar_y   = H - bar_h - 5
        bar_w   = W - 12
        bar_x   = 6
        fill_w  = int(bar_w * self._value / 100)

        p.setBrush(QBrush(qcol(C.BAR_BG)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 2, 2)

        if self._value > 85:
            bar_col = qcol(C.RED)
        elif self._value > 65:
            bar_col = qcol(C.ACC)
        else:
            bar_col = qcol(self._color)

        if fill_w > 0:
            p.setBrush(QBrush(bar_col))
            p.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), 2, 2)

        p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(8, 5, 50, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)

        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 4, W - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._text)

        p.end()

class LogWidget(QTextEdit):
    _sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Courier New", 9))
        self.setStyleSheet(f"""
            QTextEdit {{
                background: {_GLASS_BG};
                color: {C.TEXT};
                border: 1px solid {_GLASS_BORDER};
                border-radius: {_GLASS_RADIUS}px;
                padding: 8px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 8px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {_GLASS_BORDER};
                border-radius: 4px;
                min-height: 20px;
            }}
        """)
        self._queue: list[str] = []
        self._typing  = False
        self._text    = ""
        self._pos     = 0
        self._tag     = "sys"
        self._ai_name_lc = "FRIDAY"   # updated when assistant name changes
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._sig.connect(self._enqueue)

    def append_log(self, text: str):
        self._sig.emit(text)

    def _enqueue(self, text: str):
        self._queue.append(text)
        if not self._typing:
            self._next()

    def _next(self):
        if not self._queue:
            self._typing = False
            return
        self._typing = True
        self._text   = self._queue.pop(0)
        self._pos    = 0
        tl = self._text.lower()
        _ai_pfx = f"{self._ai_name_lc}:"
        if   tl.startswith("you:"):                              self._tag = "you"
        elif tl.startswith(_ai_pfx) or tl.startswith("FRIDAY:"): self._tag = "ai"
        elif tl.startswith("file:"):                             self._tag = "file"
        elif "err" in tl:                                        self._tag = "err"
        else:                                                    self._tag = "sys"
        self._tmr.start(6)

    def _step(self):
        if self._pos < len(self._text):
            ch  = self._text[self._pos]
            cur = self.textCursor()
            fmt = cur.charFormat()
            col = {
                "you":  qcol(C.WHITE),
                "ai":   qcol(C.PRI),
                "err":  qcol(C.RED),
                "file": qcol(C.GREEN),
                "sys":  qcol(C.ACC2),
            }.get(self._tag, qcol(C.TEXT))
            fmt.setForeground(QBrush(col))
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText(ch, fmt)
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            self._pos += 1
        else:
            self._tmr.stop()
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText("\n")
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            QTimer.singleShot(20, self._next)

_FILE_ICONS = {
    "image":   ("🖼", "#00d4ff"), "video":   ("🎬", "#ff6b00"),
    "audio":   ("🎵", "#cc44ff"), "pdf":     ("📄", "#ff4444"),
    "word":    ("📝", "#4488ff"), "excel":   ("📊", "#44bb44"),
    "code":    ("💻", "#ffcc00"), "archive": ("📦", "#ff8844"),
    "pptx":    ("📊", "#ff6622"), "text":    ("📃", "#aaaaaa"),
    "data":    ("🔧", "#88ddff"), "unknown": ("📎", "#888888"),
}
_EXT_TO_CAT = {
    **dict.fromkeys(["jpg","jpeg","png","gif","webp","bmp","tiff","svg","ico"], "image"),
    **dict.fromkeys(["mp4","avi","mov","mkv","wmv","flv","webm","m4v"],         "video"),
    **dict.fromkeys(["mp3","wav","ogg","m4a","aac","flac","wma","opus"],        "audio"),
    **dict.fromkeys(["pdf"],                                                     "pdf"),
    **dict.fromkeys(["doc","docx"],                                              "word"),
    **dict.fromkeys(["xls","xlsx","ods"],                                        "excel"),
    **dict.fromkeys(["ppt","pptx"],                                              "pptx"),
    **dict.fromkeys(["py","js","ts","jsx","tsx","html","css","java","c","cpp",
                     "cs","go","rs","rb","php","swift","kt","sh","sql","lua"],   "code"),
    **dict.fromkeys(["zip","rar","tar","gz","7z","bz2","xz"],                   "archive"),
    **dict.fromkeys(["txt","md","rst","log"],                                    "text"),
    **dict.fromkeys(["csv","tsv","json","xml"],                                  "data"),
}

def _file_category(path: Path) -> str:
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")

def _fmt_size(size: int) -> str:
    if   size < 1024:    return f"{size} B"
    elif size < 1024**2: return f"{size/1024:.1f} KB"
    elif size < 1024**3: return f"{size/1024**2:.1f} MB"
    else:                return f"{size/1024**3:.1f} GB"


class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(100)
        self._current_file: str | None = None
        self._hovering  = False
        self._drag_over = False
        self._dash_offset = 0.0
        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._animate)
        self._anim_tmr.start(40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = _DropCanvas(self)
        layout.addWidget(self._canvas)

    def _animate(self):
        self._dash_offset = (self._dash_offset + 0.8) % 20
        self._canvas.update()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True; self._canvas.update()

    def dragLeaveEvent(self, e):
        self._drag_over = False; self._canvas.update()

    def dropEvent(self, e: QDropEvent):
        self._drag_over = False
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_file():
                self._set_file(path)
        self._canvas.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def enterEvent(self, e):
        self._hovering = True; self._canvas.update()

    def leaveEvent(self, e):
        self._hovering = False; self._canvas.update()

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None; self._canvas.update()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a file for FRIDAY", str(Path.home()),
            "All Files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.docx *.txt *.md *.pptx);;"
            "Data (*.csv *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)",
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        self._canvas.update()
        self.file_selected.emit(path)


class _DropCanvas(QWidget):
    def __init__(self, zone: FileDropZone):
        super().__init__(zone)
        self._z = zone

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        z    = self._z
        W, H = self.width(), self.height()
        pad  = 6
        rect = QRectF(pad, pad, W - pad * 2, H - pad * 2)

        bg_col = qcol(C.PRI, 35 if z._drag_over else (25 if z._hovering else 18))
        p.setBrush(QBrush(bg_col)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 10, 10)

        if z._current_file:   border_col = qcol(C.GREEN, 200)
        elif z._drag_over:    border_col = qcol(C.PRI, 230)
        elif z._hovering:     border_col = qcol(C.BORDER_B, 200)
        else:                 border_col = qcol(C.BORDER, 160)

        pen = QPen(border_col, 1.5, Qt.PenStyle.DashLine)
        pen.setDashOffset(z._dash_offset)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 10, 10)

        if z._current_file:   self._paint_file(p, W, H)
        elif z._drag_over:    self._paint_drag_over(p, W, H)
        else:                 self._paint_idle(p, W, H, z._hovering)

        p.end()

    def _paint_idle(self, p, W, H, hover):
        cx, cy = W / 2, H / 2
        col = qcol(C.PRI_DIM if not hover else C.PRI)
        p.setPen(QPen(col, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(cx, cy - 14), QPointF(cx, cy + 4))
        p.drawLine(QPointF(cx - 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx + 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx - 14, cy + 4), QPointF(cx + 14, cy + 4))
        p.setFont(QFont("Courier New", 8))
        p.setPen(QPen(qcol(C.PRI_DIM if not hover else C.TEXT), 1))
        p.drawText(QRectF(0, cy + 8, W, 16), Qt.AlignmentFlag.AlignCenter,
                   "Drop file here  or  Click to Browse")
        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol("#1a4a5a"), 1))
        p.drawText(QRectF(0, cy + 24, W, 14), Qt.AlignmentFlag.AlignCenter,
                   "Images · Video · Audio · PDF · Docs · Code · Data")

    def _paint_drag_over(self, p, W, H):
        cx, cy = W / 2, H / 2
        p.setFont(QFont("Courier New", 20))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy - 24, W, 32), Qt.AlignmentFlag.AlignCenter, "⬇")
        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy + 12, W, 16), Qt.AlignmentFlag.AlignCenter, "Release to load")

    def _paint_file(self, p, W, H):
        path = Path(self._z._current_file)
        cat  = _file_category(path)
        icon, icon_col = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size_str = _fmt_size(path.stat().st_size)
        ext_str  = path.suffix.upper().lstrip(".") or "FILE"

        block_x, block_w = 10, 60
        p.setFont(QFont("Segoe UI Emoji", 22) if _OS == "Windows" else QFont("Arial", 22))
        p.setPen(QPen(qcol(icon_col), 1))
        p.drawText(QRectF(block_x, 0, block_w, H), Qt.AlignmentFlag.AlignCenter, icon)

        tx = block_x + block_w + 6
        tw = W - tx - 38

        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        name = path.name if len(path.name) <= 34 else path.name[:31] + "..."
        p.drawText(QRectF(tx, H * 0.18, tw, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(tx, H * 0.18 + 18, tw, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{ext_str}  ·  {size_str}")

        p.setFont(QFont("Courier New", 6))
        p.setPen(QPen(qcol("#1e5c6a"), 1))
        par = str(path.parent)
        if len(par) > 42: par = "…" + par[-41:]
        p.drawText(QRectF(tx, H * 0.18 + 34, tw, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, par)

        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.RED, 180), 1))
        p.drawText(QRectF(W - 34, 0, 28, H), Qt.AlignmentFlag.AlignCenter, "✕")

    def mousePressEvent(self, e):
        z = self._z
        if z._current_file and e.pos().x() > self.width() - 34:
            z.clear_file()
        else:
            z.mousePressEvent(e)


class _CameraPreview(QWidget):
    """Floating overlay that briefly shows what the camera captured."""

    _W, _H = 244, 188

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            _CameraPreview {{
                {_glass_overlay_ss(10, accent=_GLASS_BORDER_HI)}
            }}
        """)
        self.setFixedWidth(self._W)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 5, 6, 6)
        lay.setSpacing(4)

        hdr = QHBoxLayout()
        title = QLabel("◈  VISUAL INPUT")
        title.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(title)
        hdr.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(16, 16)
        close_btn.setFont(QFont("Courier New", 8))
        close_btn.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent; border: none;"
        )
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.hide)
        hdr.addWidget(close_btn)
        lay.addLayout(hdr)

        self._img_lbl = QLabel()
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_lbl.setStyleSheet("background: transparent;")
        lay.addWidget(self._img_lbl)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

        self.hide()

    def show_frame(self, img_bytes: bytes) -> None:
        px = QPixmap()
        px.loadFromData(img_bytes)
        if not px.isNull():
            max_w = self._W - 12
            scaled = px.scaled(
                max_w, 160,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._img_lbl.setPixmap(scaled)
            self._img_lbl.setFixedSize(scaled.width(), scaled.height())
            self.adjustSize()
        self.show()
        self.raise_()
        self._timer.start(6_000)   # auto-dismiss after 6 s


class _HandsOrbLayer(QWidget):
    """Paints only the FRIDAY ring so camera frames are not copied for the orb."""

    def __init__(self, board: "HandsBoardWidget"):
        super().__init__(board._view)
        self._board = board
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def paintEvent(self, _):
        board = self._board
        src = board._last_frame
        if src is None or src.isNull():
            return
        from actions.hands_tracker import _card_rect
        cards = [c for c in board.engine.snapshot() if c.kind == "ring"]
        if not cards:
            return
        vw, vh = self.width(), self.height()
        iw, ih = src.width(), src.height()
        if iw < 2 or ih < 2 or vw < 2 or vh < 2:
            return
        scale = min(vw / iw, vh / ih)
        dw, dh = iw * scale, ih * scale
        ox = (vw - dw) * 0.5
        oy = (vh - dh) * 0.5
        x1, y1, x2, y2 = _card_rect(cards[0], iw, ih)
        cx = ox + (x1 + x2) * 0.5 * scale
        cy = oy + (y1 + y2) * 0.5 * scale
        fw = max(56.0, min(x2 - x1, y2 - y1) * scale)
        hud = getattr(board.window(), "hud", None)
        st = board.engine.ring_state
        speaking = st == "speaking" or (
            hud is not None and (hud.speaking or hud.state == "SPEAKING")
        )
        muted = bool(hud.muted) if hud is not None else st == "idle"
        if hud is not None and hud.state:
            state = hud.state
        else:
            state = {
                "idle": "STANDBY", "listening": "LISTENING",
                "thinking": "THINKING", "speaking": "SPEAKING",
            }.get(st, "LISTENING")
        if muted:
            accent = C.MUTED_C
        elif speaking or state == "SPEAKING":
            accent = C.SPEAK
        elif state in ("THINKING", "PROCESSING") or st == "thinking":
            accent = C.ACC2
        else:
            accent = C.PRI
        t = time.monotonic()
        if hud is not None:
            amp = float(hud._amp_disp)
            pulse = float(hud._scale)
            tick = int(hud._tick)
            rings = list(hud._rings)
            spin_y = float(hud._spin_y)
            spin_x = float(hud._spin_x)
            pulses = list(hud._pulses)
            blink = bool(hud._blink)
        else:
            amp = (0.45 + 0.45 * abs(math.sin(t * 6.0))) if speaking else 0.08
            pulse = 1.0 + amp * 0.03
            tick = int(t * 60)
            rings = [(t * 34.4) % 360, 120.0, 240.0]
            spin_y = t * 0.75
            spin_x = 0.22 + t * 0.25
            pulses = []
            blink = int(t * 2) % 2 == 0
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        paint_friday_orb(
            p, cx, cy, fw,
            accent=accent, amp=amp, pulse=pulse,
            speaking=speaking, muted=muted, state=state,
            tick=tick, rings=rings, spin_y=spin_y, spin_x=spin_x,
            pulses=pulses, particles=None, blink=blink, detail="lite",
        )
        p.end()


class HandsBoardWidget(QWidget):
    """Local MediaPipe 21-point skeleton board. OpenCV draws; HUD shows the camera."""

    _status_sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        from actions.hands_tracker import HandsBoard

        self.setStyleSheet(f"background: transparent; {_glass_ss(10)}")
        self.setAcceptDrops(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        hdr = QHBoxLayout()
        hdr.setContentsMargins(8, 5, 8, 5)
        title = QLabel("◈  HANDS BOARD")
        title.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(title)
        self._hint = QLabel("Drop a file on the glass · pinch · clap · O open · C cam · R reset")
        self._hint.setFont(QFont("Courier New", 7))
        self._hint.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr.addWidget(self._hint, stretch=1)
        close_btn = QPushButton("✕  CLOSE")
        close_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                color: {C.TEXT_DIM}; background: transparent;
                border: none; padding: 2px 6px;
            }}
            QPushButton:hover {{ color: {C.PRI}; }}
        """)
        close_btn.clicked.connect(self._request_close)
        hdr.addWidget(close_btn)
        lay.addLayout(hdr)

        self._view = QLabel(self)
        self._view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._view.setStyleSheet("background: #000;")
        self._view.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._view.setAcceptDrops(True)
        self._view.installEventFilter(self)
        lay.addWidget(self._view, stretch=1)

        self.engine = HandsBoard()
        self._on_closed = None
        self._running = False
        self._stop = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._last_frame: QPixmap | None = None
        self._pending = None
        self._need_scale = True
        self._orb_layer = _HandsOrbLayer(self)
        self._orb_tmr = QTimer(self)
        self._orb_tmr.setInterval(33)
        self._orb_tmr.timeout.connect(self._present_board_frame)
        self._status_sig.connect(self._hint.setText)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        for key, slot in (
            ("C", self.engine.cycle_camera),
            ("D", self.engine.toggle_debug),
            ("R", self.engine.reset),
            ("O", self._pick_file),
        ):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(slot)

    def eventFilter(self, obj, event):
        if obj is self._view:
            et = event.type()
            if et == QEvent.Type.Resize:
                self._orb_layer.setGeometry(self._view.rect())
                self._need_scale = True
            elif et == QEvent.Type.DragEnter:
                self.dragEnterEvent(event)
                return True
            if et == QEvent.Type.Drop:
                self.dropEvent(event)
                return True
        return super().eventFilter(obj, event)

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent):
        urls = e.mimeData().urls() if e.mimeData() else []
        n = 0
        for url in urls:
            path = url.toLocalFile()
            if not path:
                s = url.toString()
                if s.startswith("file:"):
                    path = s.replace("file:///", "").replace("file://", "")
            if path and Path(path).is_file():
                self.engine.add_image(path)
                n += 1
                if n >= 8:
                    break
        if n:
            self.engine.toast(f"on the glass · {n} file{'s' if n != 1 else ''}")
        e.acceptProposedAction()

    def _pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Put on the glass",
            str(Path.home() / "Desktop"),
            "Media (*.png *.jpg *.jpeg *.webp *.gif *.bmp *.glb *.gltf *.obj);;All files (*.*)",
        )
        if path:
            self.engine.add_image(path)

    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            return
        self._idle.wait(2.0)
        if not self.engine.snapshot():
            self.engine.reset_starter()
        self._stop.clear()
        self._idle.clear()
        self._running = True
        self.setFocus()
        self._hint.setText("Starting camera + skeleton tracker…")
        self._orb_layer.setGeometry(self._view.rect())
        self._orb_tmr.start()
        threading.Thread(target=self._loop, daemon=True, name="hands-board").start()

    def _stash_frame(self, data: bytes, width: int, height: int) -> None:
        # Worker thread: keep only the latest RGB frame so the UI never queues.
        self._pending = (data, width, height)

    def _loop(self) -> None:
        from actions.hands_tracker import run_loop
        try:
            err = run_loop(self.engine, self._stop, self._stash_frame)
            if err:
                self._status_sig.emit(err)
                print(f"[Hands] {err}")
        finally:
            self._running = False
            self._idle.set()

    def _present_board_frame(self) -> None:
        pending = self._pending
        self._pending = None
        if pending is not None:
            data, w, h = pending
            img = QImage(data, w, h, w * 3, QImage.Format.Format_RGB888)
            if not img.isNull():
                self._last_frame = QPixmap.fromImage(img.copy())
                self._need_scale = True
        src = self._last_frame
        if src is None or src.isNull():
            self._orb_layer.update()
            return
        if self._need_scale:
            vw, vh = self._view.width(), self._view.height()
            if vw > 1 and vh > 1:
                self._view.setPixmap(
                    src.scaled(
                        vw, vh,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.FastTransformation,
                    )
                )
            else:
                self._view.setPixmap(src)
            self._need_scale = False
        if self._running:
            banner = self.engine.banner()
            if banner != self._hint.text():
                self._hint.setText(banner)
        self._orb_layer.setGeometry(self._view.rect())
        self._orb_layer.raise_()
        self._orb_layer.update()

    def _request_close(self) -> None:
        self.stop()
        if callable(self._on_closed):
            try:
                self._on_closed()
            except Exception:
                pass

    def stop(self) -> None:
        self._running = False
        self._stop.set()
        self._orb_tmr.stop()
        self._pending = None
        self._last_frame = None
        self._view.clear()
        self._orb_layer.update()


class SetupOverlay(QWidget):
    done = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                {_glass_overlay_ss(12)}
            }}
        """)

        detected = {"darwin": "mac", "windows": "windows"}.get(
            _OS.lower(), "linux"
        )
        self._sel_os = detected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(8)

        def _lbl(txt, font_size=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", font_size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        layout.addWidget(_lbl("◈  INITIALISATION REQUIRED", 13, True))
        layout.addWidget(_lbl("Configure J.A.R.V.I.S. before first boot.", 9, color=C.PRI_DIM))
        layout.addSpacing(6)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep)
        layout.addSpacing(4)

        layout.addWidget(_lbl("GEMINI API KEY", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("AIza…")
        self._key_input.setFont(QFont("Courier New", 10))
        self._key_input.setFixedHeight(32)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: rgba(0, 10, 18, 0.38); color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        layout.addWidget(self._key_input)
        layout.addSpacing(12)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep2)
        layout.addSpacing(4)

        layout.addWidget(_lbl("OPERATING SYSTEM", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        det_name = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}[detected]
        layout.addWidget(_lbl(f"Auto-detected: {det_name}", 8, color=C.ACC2,
                               align=Qt.AlignmentFlag.AlignLeft))

        os_row = QHBoxLayout(); os_row.setSpacing(6)
        self._os_btns: dict[str, QPushButton] = {}
        for key, label in [("windows","⊞  Windows"),("mac","  macOS"),("linux","🐧  Linux")]:
            btn = QPushButton(label)
            btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
            btn.setFixedHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k))
            os_row.addWidget(btn)
            self._os_btns[key] = btn
        layout.addLayout(os_row)
        self._sel(detected)
        layout.addSpacing(12)

        init_btn = QPushButton("▸  INITIALISE SYSTEMS")
        init_btn.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        init_btn.setFixedHeight(36)
        init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        init_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; border: 1px solid {C.PRI};
            }}
        """)
        init_btn.clicked.connect(self._submit)
        layout.addWidget(init_btn)

    def _sel(self, key: str):
        self._sel_os = key
        pal = {"windows":(C.PRI,"#001a22"),"mac":(C.ACC2,"#1a1400"),"linux":(C.GREEN,"#001a0d")}
        for k, btn in self._os_btns.items():
            if k == key:
                fg, bg = pal[k]
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {fg}; color: {bg};
                        border: none; border-radius: 3px; font-weight: bold;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: rgba(0, 10, 18, 0.38); color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 3px;
                    }}
                    QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
                """)

    def _submit(self):
        key = self._key_input.text().strip()
        if not key:
            self._key_input.setStyleSheet(
                self._key_input.styleSheet() +
                f" QLineEdit {{ border: 1px solid {C.RED}; }}"
            )
            return
        self.done.emit(key, self._sel_os)


class HueWheel(QWidget):
    """
    Dairesel renk seçici. Kullanıcı tutamacı (küçük beyaz daire) çarkın
    çevresinde sürükleyerek TÜM renk tonları arasından seçim yapar.
    Merkezdeki dolu daire seçilen rengin canlı önizlemesidir.
    """

    hue_picked    = pyqtSignal(str)   # sürükleme sırasında (canlı)
    hue_committed = pyqtSignal(str)   # tutamaç bırakıldığında

    _RING = 16   # halka kalınlığı (px)

    def __init__(self, initial_hex: str = DEFAULT_UI_COLOR, parent=None):
        super().__init__(parent)
        self.setFixedSize(148, 148)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hue  = 0.53
        self._drag = False
        self.set_color(initial_hex)

    # ── API ──────────────────────────────────────────────────────────────────
    def color(self) -> str:
        return QColor.fromHsvF(self._hue, 1.0, 1.0).name()

    def set_color(self, hex_str: str):
        c = QColor((hex_str or "").strip())
        if c.isValid() and c.hsvHueF() >= 0:
            self._hue = c.hsvHueF()
            self.update()

    # ── geometri yardımcıları ────────────────────────────────────────────────
    def _ring_rect(self) -> QRectF:
        m = self._RING / 2 + 3
        return QRectF(self.rect()).adjusted(m, m, -m, -m)

    def _hue_from_pos(self, pos: QPointF) -> float:
        c  = QRectF(self.rect()).center()
        dx = pos.x() - c.x()
        dy = c.y() - pos.y()          # ekran y'si aşağı — matematiksel eksene çevir
        ang = math.atan2(dy, dx)      # [-π, π], saat yönünün tersi
        return (ang / (2 * math.pi)) % 1.0

    # ── çizim ────────────────────────────────────────────────────────────────
    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect   = self._ring_rect()
        center = rect.center()

        grad = QConicalGradient(center, 0)
        for i in range(0, 361, 20):
            grad.setColorAt(i / 360.0, QColor.fromHsvF((i % 360) / 360.0, 1.0, 1.0))
        p.setPen(QPen(QBrush(grad), self._RING))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect)

        # merkez önizleme dairesi
        preview = QColor.fromHsvF(self._hue, 1.0, 1.0)
        inner   = rect.adjusted(30, 30, -30, -30)
        p.setPen(QPen(qcol(C.BORDER_B), 1))
        p.setBrush(QBrush(preview))
        p.drawEllipse(inner)

        # sürüklenen tutamaç
        r   = rect.width() / 2
        ang = self._hue * 2 * math.pi
        hx  = center.x() + r * math.cos(ang)
        hy  = center.y() - r * math.sin(ang)
        p.setPen(QPen(QColor("#00060a"), 2))
        p.setBrush(QBrush(QColor("#ffffff")))
        p.drawEllipse(QPointF(hx, hy), 7.5, 7.5)
        p.end()

    # ── fare ─────────────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        self._drag = True
        self._hue  = self._hue_from_pos(e.position())
        self.update()
        self.hue_picked.emit(self.color())

    def mouseMoveEvent(self, e):
        if self._drag:
            self._hue = self._hue_from_pos(e.position())
            self.update()
            self.hue_picked.emit(self.color())

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = False
            self.hue_committed.emit(self.color())


class CustomizeOverlay(QWidget):
    """Floating overlay — change assistant name, user name, UI colour and voice."""

    saved = pyqtSignal(str, str, str, str)   # assistant_name, user_name, ui_color, voice
    _OW, _OH = 400, 588

    def __init__(self, assistant_name="FRIDAY", user_name="",
                 ui_color=DEFAULT_UI_COLOR, voice="", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            CustomizeOverlay {{
                {_glass_overlay_ss(12)}
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 18)
        lay.setSpacing(8)

        def _lbl(txt, fs=9, bold=False, color=C.PRI, align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt); w.setAlignment(align)
            w.setFont(QFont("Courier New", fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        _fs = (f"QLineEdit {{ background: rgba(0, 10, 18, 0.38); color: {C.TEXT}; "
               f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
               f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}")

        lay.addWidget(_lbl("⚙  CUSTOMISE ASSISTANT", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        lay.addWidget(_lbl("ASSISTANT NAME", 8, color=C.TEXT_DIM,
                            align=Qt.AlignmentFlag.AlignLeft))
        self._name_input = QLineEdit(assistant_name)
        self._name_input.setFont(QFont("Courier New", 10))
        self._name_input.setFixedHeight(32)
        self._name_input.setStyleSheet(_fs)
        lay.addWidget(self._name_input)

        lay.addSpacing(4)
        lay.addWidget(_lbl("YOUR NAME  (leave blank for default sir / efendim)", 8,
                            color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        self._user_input = QLineEdit(user_name)
        self._user_input.setPlaceholderText("e.g.  Tony   (leave blank for auto)")
        self._user_input.setFont(QFont("Courier New", 10))
        self._user_input.setFixedHeight(32)
        self._user_input.setStyleSheet(_fs)
        lay.addWidget(self._user_input)

        # ── Assistant voice — Gemini prebuilt voices ─────────────────────────
        # Names are language-neutral proper nouns, so the row reads the same in
        # every locale. Selecting one and applying rebuilds the Live session.
        from memory.config_manager import AVAILABLE_VOICES, DEFAULT_VOICE
        lay.addSpacing(4)
        lay.addWidget(_lbl("ASSISTANT VOICE", 8, color=C.TEXT_DIM,
                            align=Qt.AlignmentFlag.AlignLeft))
        self._sel_voice   = (voice or DEFAULT_VOICE)
        if self._sel_voice not in AVAILABLE_VOICES:
            self._sel_voice = DEFAULT_VOICE
        self._voice_btns: dict[str, QPushButton] = {}
        voice_row = QHBoxLayout(); voice_row.setSpacing(4)
        for _v in AVAILABLE_VOICES:
            b = QPushButton(_v)
            b.setCheckable(True)
            b.setFixedHeight(28)
            b.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, name=_v: self._on_voice_pick(name))
            self._voice_btns[_v] = b
            voice_row.addWidget(b)
        lay.addLayout(voice_row)
        self._refresh_voice_btns()

        # ── UI colour — renk çarkı ───────────────────────────────────────────
        lay.addSpacing(4)
        clr_hdr = QHBoxLayout()
        clr_hdr.addWidget(_lbl("UI COLOUR  —  drag the handle", 8,
                               color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        clr_hdr.addStretch()
        df_btn = QPushButton("DEFAULT")
        df_btn.setFixedSize(64, 20)
        df_btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        df_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        df_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        df_btn.clicked.connect(lambda: self._set_color(DEFAULT_UI_COLOR))
        clr_hdr.addWidget(df_btn)
        lay.addLayout(clr_hdr)

        self._initial_color = (ui_color or DEFAULT_UI_COLOR).strip().lower()
        self._sel_color     = self._initial_color
        self.on_preview     = None   # callable(hex) — canlı önizleme; MainWindow bağlar

        self._wheel = HueWheel(self._sel_color)
        wheel_row = QHBoxLayout()
        wheel_row.addStretch(); wheel_row.addWidget(self._wheel); wheel_row.addStretch()
        lay.addLayout(wheel_row)
        self._wheel.hue_picked.connect(self._on_wheel_pick)
        self._wheel.hue_committed.connect(self._on_wheel_commit)

        self._hex_input = QLineEdit(self._sel_color)
        self._hex_input.setPlaceholderText("#ff6600   (custom hex colour)")
        self._hex_input.setFont(QFont("Courier New", 10))
        self._hex_input.setFixedHeight(28)
        self._hex_input.setStyleSheet(_fs)
        self._hex_input.textEdited.connect(self._on_hex_edited)
        lay.addWidget(self._hex_input)

        lay.addSpacing(6)
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)

        save_btn = QPushButton("▸  APPLY CHANGES")
        save_btn.setFixedHeight(34)
        save_btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)

        cancel_btn = QPushButton("CANCEL")
        cancel_btn.setFixedHeight(34)
        cancel_btn.setFont(QFont("Courier New", 9))
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        cancel_btn.clicked.connect(self._cancel)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

    # ── ses seçimi ───────────────────────────────────────────────────────────
    def _on_voice_pick(self, name: str):
        self._sel_voice = name
        self._refresh_voice_btns()

    def _refresh_voice_btns(self):
        """Highlight the selected voice pill; dim the rest."""
        for name, b in self._voice_btns.items():
            on = (name == self._sel_voice)
            b.setChecked(on)
            if on:
                b.setStyleSheet(f"""
                    QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI};
                        border: 1px solid {C.PRI}; border-radius: 3px; }}
                """)
            else:
                b.setStyleSheet(f"""
                    QPushButton {{ background: transparent; color: {C.TEXT_MED};
                        border: 1px solid {C.BORDER}; border-radius: 3px; }}
                    QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
                """)

    # ── renk akışı ───────────────────────────────────────────────────────────
    def _set_color(self, hx: str, update_wheel: bool = True, preview: bool = True):
        """Seçili rengi günceller; hex kutusu + çark senkron kalır, tema canlı önizlenir."""
        self._sel_color = hx.strip().lower()
        self._hex_input.blockSignals(True)
        self._hex_input.setText(self._sel_color)
        self._hex_input.blockSignals(False)
        if update_wheel:
            self._wheel.set_color(self._sel_color)
        if preview and self.on_preview:
            self.on_preview(self._sel_color)

    def _on_wheel_pick(self, hx: str):
        # Sürükleme sırasında: hex kutusunu güncelle, temayı henüz uygulama
        self._sel_color = hx
        self._hex_input.blockSignals(True)
        self._hex_input.setText(hx)
        self._hex_input.blockSignals(False)

    def _on_wheel_commit(self, hx: str):
        # Tutamaç bırakıldı → tüm arayüzü canlı önizle
        self._set_color(hx, update_wheel=False)

    def _on_hex_edited(self, text: str):
        t = text.strip().lower()
        if t.startswith("#") and len(t) == 7:
            try:
                int(t[1:], 16)
            except ValueError:
                return
            self._set_color(t, update_wheel=True, preview=True)

    def _cancel(self):
        # Önizleme uygulandıysa açılıştaki renge geri dön
        if self.on_preview and self._sel_color != self._initial_color:
            self.on_preview(self._initial_color)
        self.hide()

    def _save(self):
        name = self._name_input.text().strip() or "FRIDAY"
        user = self._user_input.text().strip()
        self.saved.emit(name, user, self._sel_color or DEFAULT_UI_COLOR, self._sel_voice)
        self.hide()


class PluginManagerOverlay(QWidget):
    """Floating overlay — lists discovered plugins with per-plugin ON/OFF toggles."""

    _OW = 420

    def __init__(self, plugins: list[dict], parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            PluginManagerOverlay {{
                {_glass_ss(12, heavy=True)}
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        hdr = QLabel("🧩  PLUGIN MANAGER")
        hdr.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(hdr)
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        if not plugins:
            empty = QLabel("No plugins found in /plugins.")
            empty.setFont(QFont("Courier New", 8))
            empty.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(empty)

        for p in plugins:
            lay.addLayout(self._build_row(p))

        lay.addSpacing(4)
        close_btn = QPushButton("CLOSE")
        close_btn.setFixedHeight(30)
        close_btn.setFont(QFont("Courier New", 9))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self.hide)
        lay.addWidget(close_btn)
        self.adjustSize()

    def _build_row(self, p: dict) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(6)

        label_text = p["name"] if p["valid"] else f"{p['name']}  (⚠ {p['file']})"
        lbl = QLabel(label_text)
        lbl.setFont(QFont("Courier New", 8))
        lbl.setStyleSheet(f"color: {C.TEXT if p['valid'] else C.TEXT_DIM}; background: transparent;")
        lbl.setToolTip(p["description"] if p["valid"] else p["error"])
        lbl.setWordWrap(False)
        row.addWidget(lbl, stretch=1)

        btn = QPushButton()
        btn.setFixedSize(72, 24)
        btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        if not p["valid"]:
            btn.setText("BROKEN")
            btn.setEnabled(False)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
            """)
        else:
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._style_toggle(btn, p["enabled"])
            btn.clicked.connect(lambda _, name=p["name"], b=btn: self._toggle(name, b))
        row.addWidget(btn)
        return row

    def _style_toggle(self, btn: QPushButton, enabled: bool):
        if enabled:
            btn.setText("ON")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            btn.setText("OFF")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
            """)

    def _toggle(self, name: str, btn: QPushButton):
        from memory.config_manager import get_plugin_enabled, save_plugin_enabled
        new_val = not get_plugin_enabled(name)
        save_plugin_enabled(name, new_val)
        self._style_toggle(btn, new_val)


class _HudOverlay(QWidget):
    """Base for the floating panels placed by hand over the HUD.

    They are children of the central widget but sit in no layout, so Qt never
    invalidates the region they occupy when they hide or shrink: the HUD keeps
    painting around them and their last frame stays on screen as a ghost. Any
    overlay positioned with _centre_overlay needs this."""

    def hideEvent(self, e):
        p = self.parentWidget()
        if p is not None:
            # Repaint exactly what we were covering, before we stop covering it.
            p.update(self.geometry())
        super().hideEvent(e)

    def closeEvent(self, e):
        p = self.parentWidget()
        if p is not None:
            p.update(self.geometry())
        super().closeEvent(e)


class _ScreenShareBadge(_HudOverlay):
    """Compact LIVE SCREEN pill — no preview (that would recurse into the feed)."""

    _W, _H = 228, 36

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            _ScreenShareBadge {{
                background: rgba(20, 6, 0, 0.72);
                border: 1px solid rgba(255, 51, 85, 0.45);
                border-radius: 10px;
            }}
        """)
        self.setFixedSize(self._W, self._H)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 6, 4)
        lay.setSpacing(8)

        self._dot = QLabel("●")
        self._dot.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        self._dot.setStyleSheet(f"color: {C.RED}; background: transparent;")
        lay.addWidget(self._dot)

        title = QLabel("LIVE SCREEN")
        title.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.TEXT}; background: transparent; letter-spacing: 1px;")
        lay.addWidget(title)
        lay.addStretch()

        stop = QPushButton("STOP")
        stop.setFixedSize(52, 22)
        stop.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        stop.setCursor(Qt.CursorShape.PointingHandCursor)
        stop.setStyleSheet(f"""
            QPushButton {{
                color: {C.RED}; background: transparent;
                border: 1px solid rgba(255, 51, 85, 0.45); border-radius: 6px;
            }}
            QPushButton:hover {{
                color: {C.TEXT}; border-color: {C.RED};
                background: rgba(255, 51, 85, 0.12);
            }}
        """)
        stop.clicked.connect(self._on_stop)
        lay.addWidget(stop)

        self.on_stop = None  # set by MainWindow → FridayLive
        self._pulse_on = True
        self._pulse = QTimer(self)
        self._pulse.timeout.connect(self._tick_pulse)
        self.hide()

    def set_active(self, on: bool) -> None:
        if on:
            self._pulse_on = True
            self._dot.setStyleSheet(f"color: {C.RED}; background: transparent;")
            self._pulse.start(500)
            self.show()
            self.raise_()
        else:
            self._pulse.stop()
            self.hide()

    def _tick_pulse(self) -> None:
        self._pulse_on = not self._pulse_on
        col = C.RED if self._pulse_on else C.ACC
        self._dot.setStyleSheet(f"color: {col}; background: transparent;")

    def _on_stop(self) -> None:
        if callable(self.on_stop):
            self.on_stop()


class ConfirmBanner(_HudOverlay):
    """The gate in front of an action that cannot be taken back.

    The old confirmation was a tool parameter the model filled in itself, which
    means it confirmed its own shutdown requests. This is the interface asking,
    and the answer travels from a human finger to core/confirm.py without the
    model in the loop. Nothing blocks while it is up: the assistant keeps
    talking, so this costs no latency — unlike the old gate, which spent two
    tool round trips on every power command."""

    answered = pyqtSignal(bool)
    _OW = 430

    def __init__(self, title: str, detail: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ConfirmBanner {{
                background: rgba(20, 6, 0, 0.62);
                border: 1px solid rgba(255, 107, 0, 0.45);
                border-radius: {_GLASS_RADIUS}px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(8)

        hdr = QLabel("⚠  CONFIRM")
        hdr.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.ACC}; background: transparent;")
        lay.addWidget(hdr)

        ttl = QLabel(title)
        ttl.setWordWrap(True)
        ttl.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        ttl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        lay.addWidget(ttl)

        if detail:
            dtl = QLabel(detail)
            dtl.setWordWrap(True)
            dtl.setFont(QFont("Courier New", 8))
            dtl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            lay.addWidget(dtl)

        row = QHBoxLayout(); row.setSpacing(8)

        yes = QPushButton("▸  CONFIRM")
        yes.setFixedHeight(32)
        yes.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        yes.setCursor(Qt.CursorShape.PointingHandCursor)
        yes.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.ACC};
                border: 1px solid {C.ACC}; border-radius: 3px; }}
            QPushButton:hover {{ background: rgba(255,107,0,40); }}
        """)
        yes.clicked.connect(lambda: self.answered.emit(True))
        row.addWidget(yes)

        no = QPushButton("CANCEL")
        no.setFixedHeight(32)
        no.setFont(QFont("Courier New", 9))
        no.setCursor(Qt.CursorShape.PointingHandCursor)
        no.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        no.clicked.connect(lambda: self.answered.emit(False))
        row.addWidget(no)
        lay.addLayout(row)

        # Default focus on CANCEL: if someone hits Enter without reading, the
        # safe answer wins.
        no.setDefault(True)
        no.setFocus()


class _CallAvatar(QWidget):
    """Initial in a ring — pulses so the card reads as a live call, not a dialog."""

    _S = 88

    def __init__(self, letter: str, inbound: bool, parent=None):
        super().__init__(parent)
        self.setFixedSize(self._S, self._S)
        self._letter = (letter or "?").strip().upper()[:1] or "?"
        self._inbound = inbound
        self._phase = 0.0
        tmr = QTimer(self)
        tmr.timeout.connect(self._tick)
        tmr.start(33)

    def _tick(self) -> None:
        self._phase = (self._phase + 0.07) % (2 * math.pi)
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx = cy = self._S / 2
        accent = QColor(C.GREEN if self._inbound else C.PRI)
        wave = (math.sin(self._phase) + 1) / 2
        for i in (0, 1):
            t = (wave + i * 0.45) % 1.0
            rr = 26 + t * 16
            col = QColor(accent)
            col.setAlpha(int(90 * (1 - t)))
            p.setPen(QPen(col, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(cx, cy), rr, rr)
        p.setBrush(QColor(C.PRI_GHO))
        p.setPen(QPen(accent, 2))
        p.drawEllipse(QPointF(cx, cy), 26, 26)
        p.setPen(QColor(C.WHITE))
        p.setFont(QFont("Courier New", 22, QFont.Weight.Bold))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._letter)


class CallCard(_HudOverlay):
    """WhatsApp call on the HUD — outbound confirm, or incoming accept/decline."""

    action = pyqtSignal(str)  # call | cancel | accept | decline | auto_reply
    _OW = 340

    def __init__(self, contact: str, kind: str, *, inbound: bool, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        ring = "rgba(0, 255, 136, 0.40)" if inbound else "rgba(255, 107, 0, 0.45)"
        self.setStyleSheet(f"""
            CallCard {{
                background: rgba(12, 4, 0, 0.78);
                border: 1px solid {ring};
                border-radius: 16px;
            }}
        """)
        self.setFixedWidth(self._OW)

        contact = (contact or "WhatsApp").strip() or "WhatsApp"
        kind = "video" if str(kind).lower() == "video" else "voice"
        glyph = "◉" if kind == "video" else "☎"

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(8)

        hdr = QLabel("◈  WHATSAPP  ·  " + ("INCOMING" if inbound else kind.upper()))
        hdr.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        hdr.setStyleSheet(
            f"color: {C.GREEN if inbound else C.ACC}; background: transparent; letter-spacing: 1px;"
        )
        hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(hdr)

        av_row = QHBoxLayout()
        av_row.addStretch()
        av_row.addWidget(_CallAvatar(contact[:1], inbound))
        av_row.addStretch()
        lay.addLayout(av_row)

        name = QLabel(contact.upper())
        name.setWordWrap(True)
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name.setFont(QFont("Courier New", 13, QFont.Weight.Bold))
        name.setStyleSheet(f"color: {C.WHITE}; background: transparent; letter-spacing: 2px;")
        lay.addWidget(name)

        sub = QLabel(
            f"{glyph}  {kind} call  ·  ringing" if inbound
            else f"{glyph}  {kind} call  ·  confirm to place"
        )
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setFont(QFont("Courier New", 8))
        sub.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        lay.addWidget(sub)

        lay.addSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(8)

        if inbound:
            accept = self._btn("ACCEPT", C.GREEN, "rgba(0, 255, 136, 0.16)", "accept")
            decline = self._btn("DECLINE", C.RED, "rgba(255, 51, 85, 0.14)", "decline")
            later = self._btn("MSG", C.TEXT_MED, "transparent", "auto_reply")
            later.setToolTip("Decline and send your saved auto-reply")
            row.addWidget(accept, stretch=2)
            row.addWidget(decline, stretch=2)
            row.addWidget(later, stretch=1)
            decline.setDefault(True)
            decline.setFocus()
        else:
            call = self._btn("CALL", C.GREEN, "rgba(0, 255, 136, 0.16)", "call")
            cancel = self._btn("CANCEL", C.TEXT_MED, "transparent", "cancel")
            row.addWidget(call, stretch=1)
            row.addWidget(cancel, stretch=1)
            cancel.setDefault(True)
            cancel.setFocus()
        lay.addLayout(row)

    def _btn(self, label: str, color: str, fill: str, act: str) -> QPushButton:
        b = QPushButton(label)
        b.setFixedHeight(34)
        b.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(f"""
            QPushButton {{
                background: {fill}; color: {color};
                border: 1px solid {color}; border-radius: 8px;
            }}
            QPushButton:hover {{ background: rgba(255, 255, 255, 0.06); }}
        """)
        b.clicked.connect(lambda: self.action.emit(act))
        return b


class PlanPanel(_HudOverlay):
    """The agent's step list, from proposal to completion, in one widget.

    Same trust model as ConfirmBanner: the model can *draft* a plan but only a
    human finger on APPROVE starts it. The panel then stays up as a live
    checklist — each step flips ○ → ▸ → ✔ as the agent works — with a STOP
    button that halts between steps. Destructive steps are flagged before
    approval, not discovered after."""

    answered = pyqtSignal(bool)   # approval answer, or (while running) STOP
    closed   = pyqtSignal()       # CLOSE pressed on the finished panel

    _OW = 480
    _GLYPH = {
        "pending": "○", "running": "▸", "done": "✔",
        "failed": "✖", "skipped": "⊘",
    }

    def __init__(self, goal: str, steps: list, parent=None):
        super().__init__(parent)
        self.executing = False
        self._steps = steps

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            PlanPanel {{
                {_glass_overlay_ss(12, accent=_GLASS_BORDER_HI)}
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        self._hdr = QLabel("◈  TASK PLAN — AWAITING APPROVAL")
        self._hdr.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        self._hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(self._hdr)

        goal_lbl = QLabel(goal[:160])
        goal_lbl.setWordWrap(True)
        goal_lbl.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        goal_lbl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        lay.addWidget(goal_lbl)
        lay.addSpacing(4)

        self._rows: list[QLabel] = []
        for s in steps:
            row = QLabel()
            row.setWordWrap(True)
            row.setTextFormat(Qt.TextFormat.RichText)
            row.setFont(QFont("Courier New", 9))
            row.setStyleSheet("background: transparent;")
            self._rows.append(row)
            lay.addWidget(row)
        for i in range(len(steps)):
            self.set_step(i, "pending", "")

        self._summary = QLabel("")
        self._summary.setWordWrap(True)
        self._summary.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        self._summary.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        self._summary.hide()
        lay.addSpacing(4)
        lay.addWidget(self._summary)

        row = QHBoxLayout(); row.setSpacing(8)

        self._yes = QPushButton("▸  APPROVE && RUN")
        self._yes.setFixedHeight(32)
        self._yes.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        self._yes.setCursor(Qt.CursorShape.PointingHandCursor)
        self._yes.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.GREEN};
                border: 1px solid {C.GREEN}; border-radius: 3px; }}
            QPushButton:hover {{ background: rgba(0,255,136,25); }}
        """)
        self._yes.clicked.connect(lambda: self.answered.emit(True))
        row.addWidget(self._yes)

        self._no = QPushButton("CANCEL")
        self._no.setFixedHeight(32)
        self._no.setFont(QFont("Courier New", 9))
        self._no.setCursor(Qt.CursorShape.PointingHandCursor)
        self._no.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        # False means "decline" before approval and "STOP" while running —
        # the receiving side (AgentRunner.on_plan_answer) knows which phase
        # it is in, so one signal covers both.
        self._no.clicked.connect(lambda: self.answered.emit(False))
        row.addWidget(self._no)
        lay.addLayout(row)

        # Same safety default as ConfirmBanner: Enter without reading = CANCEL.
        self._no.setDefault(True)
        self._no.setFocus()

    def _glyph_color(self, status: str) -> str:
        return {
            "pending": C.TEXT_DIM, "running": C.PRI, "done": C.GREEN,
            "failed": C.ACC, "skipped": C.TEXT_DIM,
        }.get(status, C.TEXT_DIM)

    def set_step(self, idx: int, status: str, note: str = ""):
        if not (0 <= idx < len(self._rows)):
            return
        s     = self._steps[idx]
        glyph = self._GLYPH.get(status, "○")
        col   = self._glyph_color(status)
        title_col = C.TEXT if status in ("running", "done") else C.TEXT_MED
        danger = (f"&nbsp;<span style='color:{C.ACC};'>⚠</span>"
                  if s.get("destructive") else "")
        note_html = (f"<br/><span style='color:{C.TEXT_DIM}; font-size: 8pt;'>"
                     f"&nbsp;&nbsp;&nbsp;{note[:90]}</span>" if note else "")
        self._rows[idx].setText(
            f"<span style='color:{col};'>{glyph}</span>&nbsp;"
            f"<span style='color:{title_col};'>{s.get('title','')[:90]}</span>"
            f"{danger}{note_html}"
        )

    def enter_execution(self):
        self.executing = True
        self._hdr.setText("◈  TASK RUNNING")
        self._yes.hide()
        self._no.setText("■  STOP")
        self._no.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.ACC};
                border: 1px solid {C.ACC}; border-radius: 3px; }}
            QPushButton:hover {{ background: rgba(255,107,0,40); }}
        """)

    def finish(self, summary: str, ok: bool):
        self.executing = False
        self._hdr.setText("◈  TASK COMPLETE" if ok else "◈  TASK HALTED")
        self._hdr.setStyleSheet(
            f"color: {C.GREEN if ok else C.ACC}; background: transparent;")
        self._summary.setText(summary[:220])
        self._summary.show()
        self._yes.hide()
        self._no.setText("CLOSE")
        self._no.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        try:
            self._no.clicked.disconnect()
        except Exception:
            pass
        self._no.clicked.connect(self.closed.emit)


class AudioDeviceOverlay(_HudOverlay):
    """Choose which microphone FRIDAY listens to and which speakers it uses.

    Both audio streams used to open with no `device=` at all, so they always
    took the OS default — which on Windows moves by itself the moment a headset
    is plugged in. 'FRIDAY can't hear me' is usually 'FRIDAY is listening to the
    webcam'."""

    picked = pyqtSignal()      # emitted after Apply, when something changed
    _OW = 460

    def __init__(self, parent=None):
        super().__init__(parent)
        from core.audio_devices import list_devices, DEFAULT_LABEL
        from memory.config_manager import get_input_device, get_output_device

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            AudioDeviceOverlay {{
                {_glass_ss(12, heavy=True)}
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        hdr = QLabel("🎧  AUDIO DEVICES")
        hdr.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        _combo_css = (
            f"QComboBox {{ background: rgba(0, 10, 18, 0.38); color: {C.TEXT}; "
            f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
            f"QComboBox:hover {{ border-color: {C.BORDER_B}; }}"
            f"QComboBox QAbstractItemView {{ background: rgba(0, 10, 18, 0.38); color: {C.TEXT}; "
            f"selection-background-color: {C.PRI_GHO}; border: 1px solid {C.BORDER}; }}"
        )

        def _row(label: str, kind: str, current: str) -> QComboBox:
            cap = QLabel(label)
            cap.setFont(QFont("Courier New", 8))
            cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(cap)

            box = QComboBox()
            box.setFont(QFont("Courier New", 9))
            box.setFixedHeight(30)
            box.setStyleSheet(_combo_css)
            # The list is served from a cache warmed on a background thread at
            # startup, so opening this panel never blocks the Qt thread on the
            # host audio API.
            box.addItem(DEFAULT_LABEL, "")
            for name in list_devices(kind):
                box.addItem(name, name)
            idx = box.findData(current) if current else 0
            box.setCurrentIndex(idx if idx >= 0 else 0)
            if current and idx < 0:
                # Saved device is not plugged in right now. Show it rather than
                # silently resetting the user's choice to default.
                box.addItem(f"{current}  (not connected)", current)
                box.setCurrentIndex(box.count() - 1)
            lay.addWidget(box)
            return box

        self._in_box  = _row("MICROPHONE — what FRIDAY hears you with",
                             "input", get_input_device())
        lay.addSpacing(4)
        self._out_box = _row("SPEAKERS — what FRIDAY talks through",
                             "output", get_output_device())

        note = QLabel("Applying reconnects the session. Your conversation is kept.")
        note.setWordWrap(True)
        note.setFont(QFont("Courier New", 7))
        note.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addSpacing(6)
        lay.addWidget(note)

        row = QHBoxLayout(); row.setSpacing(8)
        ok = QPushButton("▸  APPLY")
        ok.setFixedHeight(32)
        ok.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """)
        ok.clicked.connect(self._apply)
        row.addWidget(ok)

        cancel = QPushButton("CLOSE")
        cancel.setFixedHeight(32)
        cancel.setFont(QFont("Courier New", 9))
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        cancel.clicked.connect(self.hide)
        row.addWidget(cancel)
        lay.addLayout(row)

    def _apply(self):
        from memory.config_manager import (
            get_input_device, get_output_device,
            save_input_device, save_output_device,
        )
        new_in  = self._in_box.currentData()  or ""
        new_out = self._out_box.currentData() or ""
        changed = (new_in != get_input_device()) or (new_out != get_output_device())
        save_input_device(new_in)
        save_output_device(new_out)
        self.hide()
        # Only rebuild the session if something actually moved — a no-op Apply
        # should not cost a reconnect.
        if changed:
            self.picked.emit()


class MemoryOverlay(_HudOverlay):
    """Everything FRIDAY has stored about you, and when it learned it.

    Memory used to be a 2200-character store that deleted its oldest entries
    when full and mentioned it only on stdout. The cap is gone; this panel is
    the other half of that change — a memory you cannot inspect is a memory you
    cannot trust, and 'delete' has to be something the person can do."""

    _OW = 520

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            MemoryOverlay {{
                {_glass_ss(12, heavy=True)}
            }}
        """)
        self.setFixedWidth(self._OW)

        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(20, 16, 20, 16)
        self._lay.setSpacing(5)
        self._rebuild()

    def _clear_layout(self):
        """Take every item out of the layout and detach it from the widget tree
        in this call.

        deleteLater() on its own is not enough: it queues destruction for the
        next event-loop pass, and until then the old rows are still children of
        this widget and still paint — which is what drew half of the previous
        panel over the new one. setParent(None) removes them from the tree now;
        deleteLater() then frees them safely."""
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                # hide() stops it painting in this frame; deleteLater() frees it
                # safely afterwards. setParent(None) would also stop the paint,
                # but it turns the widget into a top-level window for the moment
                # between the two calls, which is not something to leave lying
                # around inside a click handler.
                w.hide()
                w.deleteLater()
                continue
            sub = item.layout()
            if sub is not None:
                while sub.count():
                    si = sub.takeAt(0)
                    sw = si.widget()
                    if sw is not None:
                        sw.hide()
                        sw.deleteLater()
                sub.deleteLater()

    def _settle(self, before):
        """Size the panel to its content, re-centre it, and repaint what the old
        size covered.

        The re-size has to happen here rather than at the end of _rebuild
        because Qt has not polished the freshly-created children at that point,
        so the size hint it would read is the empty-layout one. Measured: a
        first adjustSize() returned 32 px for a panel whose content needed 155,
        and a second call — after the same widgets had been through the event
        loop — returned 155. So this runs twice: once now, once on the next
        turn, from _rebuild.

        The re-centre and the repaint are needed because the overlay is placed
        by hand and is in no layout: shrinking it leaves it off-centre and
        leaves its former pixels on screen, since nothing tells the parent that
        region changed. The repaint has to cover the union of the old and new
        rectangles."""
        self._lay.invalidate()
        self._lay.activate()
        self.updateGeometry()
        self.adjustSize()

        p = self.parentWidget()
        if p is None:
            self.update()
            return
        self.move(max(0, (p.width()  - self.width())  // 2),
                  max(0, (p.height() - self.height()) // 2))
        p.update(before.united(self.geometry()))
        self.update()

    def _rebuild(self):
        before = self.geometry()
        self._clear_layout()

        from memory.memory_manager import all_entries_for_ui

        hdr = QLabel("🧠  WHAT FRIDAY REMEMBERS")
        hdr.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        self._lay.addWidget(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        self._lay.addWidget(sep)

        rows = all_entries_for_ui()

        cap = QLabel(f"{len(rows)} stored facts — newest first. "
                     f"Nothing here is sent anywhere; it lives in "
                     f"memory/long_term.json on this machine.")
        cap.setWordWrap(True)
        cap.setFont(QFont("Courier New", 7))
        cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._lay.addWidget(cap)

        if not rows:
            empty = QLabel("Nothing stored yet.")
            empty.setFont(QFont("Courier New", 9))
            empty.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            self._lay.addWidget(empty)
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFixedHeight(min(420, 34 * len(rows) + 10))
            scroll.setStyleSheet(
                f"QScrollArea {{ border: 1px solid {C.BORDER}; border-radius: 3px; "
                f"background: transparent; }}"
            )
            inner = QWidget()
            ilay  = QVBoxLayout(inner)
            ilay.setContentsMargins(6, 6, 6, 6)
            ilay.setSpacing(3)

            for r in rows:
                line = QHBoxLayout(); line.setSpacing(6)
                txt = QLabel(f"<b>{r['key'].replace('_', ' ')}</b> "
                             f"<span style='color:{C.TEXT_MED}'>— {r['value']}</span>")
                txt.setWordWrap(True)
                txt.setFont(QFont("Courier New", 8))
                txt.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
                line.addWidget(txt, 1)

                meta = QLabel(f"{r['category'][:4]} · {r['updated'] or '—'}")
                meta.setFont(QFont("Courier New", 7))
                meta.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
                line.addWidget(meta)

                rm = QPushButton("✕")
                rm.setFixedSize(20, 20)
                rm.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
                rm.setCursor(Qt.CursorShape.PointingHandCursor)
                rm.setToolTip("Forget this")
                rm.setStyleSheet(f"""
                    QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 3px; }}
                    QPushButton:hover {{ color: {C.RED}; border-color: {C.RED}; }}
                """)
                rm.clicked.connect(
                    lambda _=False, c=r["category"], k=r["key"]: self._forget(c, k))
                line.addWidget(rm)

                holder = QWidget()
                holder.setLayout(line)
                ilay.addWidget(holder)

            ilay.addStretch()
            scroll.setWidget(inner)
            self._lay.addWidget(scroll)

        close = QPushButton("CLOSE")
        close.setFixedHeight(30)
        close.setFont(QFont("Courier New", 9))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close.clicked.connect(self.hide)
        self._lay.addWidget(close)

        self._settle(before)
        # …and again once Qt has polished the new children, because the size
        # hint is not final until then. Harmless when the first pass already
        # got it right: _settle is idempotent.
        QTimer.singleShot(0, lambda g=before: self._settle(g))

    def _forget(self, category: str, key: str):
        from memory.memory_manager import forget
        forget(key, category)
        # Rebuild on the NEXT event-loop turn, not inside this click handler.
        # The rebuild destroys the very ✕ button that emitted this signal, and
        # Qt is entitled to touch the sender after a slot returns; tearing it
        # down mid-emission is how a widget ends up half-alive on screen.
        QTimer.singleShot(0, self._rebuild)


class ClipboardPanel(QWidget):
    """Floating panel shown when text is copied — offers quick FRIDAY actions."""

    action_requested = pyqtSignal(str)
    _W, _H = 326, 112

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ClipboardPanel {{
                {_glass_ss(10, heavy=True)}
            }}
        """)
        self.setFixedWidth(self._W)
        self._clip_text = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 7)
        lay.setSpacing(4)

        hdr = QHBoxLayout(); hdr.setSpacing(4)
        icon_lbl = QLabel("◈  CLIPBOARD DETECTED")
        icon_lbl.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        icon_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        hdr.addWidget(icon_lbl); hdr.addStretch()
        x_btn = QPushButton("✕")
        x_btn.setFixedSize(16, 16)
        x_btn.setFont(QFont("Courier New", 8))
        x_btn.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        x_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        x_btn.clicked.connect(self.hide)
        hdr.addWidget(x_btn)
        lay.addLayout(hdr)

        self._preview = QLabel()
        self._preview.setFont(QFont("Courier New", 8))
        self._preview.setStyleSheet(f"""
            color: {C.TEXT}; {_glass_input_ss()}
            padding: 4px 6px;
        """)
        self._preview.setWordWrap(False)
        self._preview.setFixedHeight(28)
        lay.addWidget(self._preview)

        btn_row = QHBoxLayout(); btn_row.setSpacing(4)
        _bs = _glass_btn_dim_ss()
        for label, cmd_fmt in [
            ("TRANSLATE", "Translate this text to English: {text}"),
            ("SUMMARISE", "Summarise this: {text}"),
            ("EXPLAIN",   "Explain this: {text}"),
            ("FIX",       "Fix grammar and spelling: {text}"),
        ]:
            b = QPushButton(label)
            b.setFixedHeight(22)
            b.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(_bs)
            b.clicked.connect(lambda _, c=cmd_fmt: self._trigger(c))
            btn_row.addWidget(b)
        lay.addLayout(btn_row)

        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self.hide)
        self.hide()

    def _trigger(self, cmd_fmt: str):
        if self._clip_text:
            self.action_requested.emit(cmd_fmt.format(text=self._clip_text[:800]))
        self.hide()

    def show_clipboard(self, text: str):
        self._clip_text = text
        preview = text[:58].replace('\n', ' ')
        if len(text) > 58:
            preview += "…"
        self._preview.setText(f'"{preview}"')
        self.show(); self.raise_()
        self._dismiss_timer.start(8000)


class RemoteKeyOverlay(QWidget):
    """Floating overlay — QR code for instant phone pairing + manual key fallback."""

    closed = pyqtSignal()

    _OW, _OH = 400, 465

    def __init__(self, url: str, key: str, auto_login_url: str = "",
                 manual_url: str = "", expiry_secs: int = 600, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            RemoteKeyOverlay {{
                {_glass_ss(14, heavy=True)}
            }}
        """)
        self._expiry          = time.time() + expiry_secs
        self._on_new_key      = None
        self._auto_login_url  = auto_login_url
        self._manual_url      = manual_url or url

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 16, 24, 16)
        lay.setSpacing(5)

        def _lbl(txt, fs=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            w.setWordWrap(True)
            return w

        lay.addWidget(_lbl("◈  REMOTE ACCESS", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep)

        # ── QR code ───────────────────────────────────────────────────────────
        self._qr_label = QLabel()
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setFixedSize(176, 176)
        self._qr_label.setStyleSheet(
            "background: white; border-radius: 10px; padding: 4px;"
        )
        qr_row = QHBoxLayout()
        qr_row.addStretch()
        qr_row.addWidget(self._qr_label)
        qr_row.addStretch()
        lay.addLayout(qr_row)

        self._update_qr(auto_login_url)

        lay.addWidget(_lbl("Scan with phone camera to connect instantly", 8, color=C.TEXT_DIM))

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep2)

        lay.addWidget(_lbl("Or enter manually:", 7, color=C.TEXT_DIM,
                           align=Qt.AlignmentFlag.AlignLeft))

        self._url_lbl = QLabel(self._manual_url)
        self._url_lbl.setFont(QFont("Courier New", 8))
        self._url_lbl.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        self._url_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._url_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self._url_lbl)

        self._key_lbl = QLabel(key)
        self._key_lbl.setFont(QFont("Courier New", 28, QFont.Weight.Bold))
        self._key_lbl.setStyleSheet(f"""
            color: {C.ACC};
            background: {C.PANEL2};
            border: 1px solid {C.BORDER_B};
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 10px;
        """)
        self._key_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._key_lbl)

        self._timer_lbl = QLabel()
        self._timer_lbl.setFont(QFont("Courier New", 8))
        self._timer_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._timer_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._timer_lbl)

        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        new_btn = QPushButton("NEW KEY")
        new_btn.setFixedHeight(32)
        new_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 5px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        new_btn.clicked.connect(self._refresh_key)
        btn_row.addWidget(new_btn)

        close_btn = QPushButton("DISMISS")
        close_btn.setFixedHeight(32)
        close_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self._do_close)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        self._ctimer = QTimer(self)
        self._ctimer.timeout.connect(self._tick)
        self._ctimer.start(1000)
        self._tick()

    def set_new_key_callback(self, fn) -> None:
        self._on_new_key = fn

    def _update_qr(self, url: str) -> None:
        if not url:
            self._qr_label.setText("—")
            return
        try:
            import qrcode as _qrmod
            from io import BytesIO
            qr = _qrmod.QRCode(
                box_size=5, border=2,
                error_correction=_qrmod.constants.ERROR_CORRECT_M,
            )
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap()
            px.loadFromData(buf.getvalue())
            self._qr_label.setPixmap(
                px.scaled(170, 170,
                          Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
            )
        except ImportError:
            self._qr_label.setText("pip install\nqrcode[pil]")
            self._qr_label.setFont(QFont("Courier New", 8))
            self._qr_label.setStyleSheet(
                "color: #888; background: white; border-radius: 10px; padding: 4px;"
            )
        except Exception:
            self._qr_label.setText(url[:28])
            self._qr_label.setFont(QFont("Courier New", 7))
            self._qr_label.setStyleSheet(
                f"color: {C.PRI}; background: white; border-radius: 10px; padding: 4px;"
            )

    def _tick(self):
        remaining = max(0, int(self._expiry - time.time()))
        m, s = divmod(remaining, 60)
        self._timer_lbl.setText(f"Key expires in  {m:02d}:{s:02d}")
        if remaining == 0:
            self._do_close()

    def mark_connected(self) -> None:
        """Call from any thread when a phone successfully connects."""
        self._ctimer.stop()
        self._key_lbl.setText("CONNECTED")
        self._key_lbl.setStyleSheet(f"""
            color: {C.GREEN};
            background: rgba(34,197,94,0.08);
            border: 2px solid rgba(34,197,94,0.4);
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 4px;
        """)
        self._qr_label.setText("✓")
        self._qr_label.setFont(QFont("Courier New", 54, QFont.Weight.Bold))
        self._qr_label.setStyleSheet(
            "color: #00ff88; background: #001a0d; border-radius: 10px;"
        )
        self._timer_lbl.setText("Phone connected — FRIDAY ready")
        self._timer_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent;")

    def _refresh_key(self):
        if self._on_new_key:
            result = self._on_new_key()
            if result:
                url    = result[0]
                key    = result[1]
                auto   = result[2] if len(result) >= 3 else ""
                manual = result[3] if len(result) >= 4 else url
                self._manual_url     = manual or url
                self._url_lbl.setText(self._manual_url)
                self._key_lbl.setText(key)
                self._auto_login_url = auto
                self._update_qr(auto or url)
                self._expiry = time.time() + 600
                self._key_lbl.setStyleSheet(f"""
                    color: {C.ACC};
                    background: {C.PANEL2};
                    border: 1px solid {C.BORDER_B};
                    border-radius: 8px;
                    padding: 6px 4px;
                    letter-spacing: 10px;
                """)
                self._timer_lbl.setStyleSheet(
                    f"color: {C.TEXT_MED}; background: transparent;"
                )
                self._ctimer.start(1000)
                self._tick()

    def _do_close(self):
        self._ctimer.stop()
        self.hide()
        self.closed.emit()


class MainWindow(QMainWindow):
    _log_sig        = pyqtSignal(str)
    _state_sig      = pyqtSignal(str)
    _content_sig    = pyqtSignal(str, str)   # (title, text) — thread-safe content display
    _reconfig_sig   = pyqtSignal()           # trigger setup overlay from any thread
    _camera_sig     = pyqtSignal(bytes)      # show camera frame preview (small overlay)
    _cam_stream_sig = pyqtSignal(bool)       # True=start live stream, False=stop
    _cam_frame_sig  = pyqtSignal(bytes)      # live camera frame → HUD area
    _hands_cmd_sig  = pyqtSignal(str, dict)  # local hands board: open/close/present/…
    _clipboard_sig  = pyqtSignal(str)        # clipboard text changed (thread-safe)
    _confirm_sig    = pyqtSignal(str, str)   # (title, detail) — irreversible-action gate
    _confirm_hide_sig = pyqtSignal()
    _call_in_sig    = pyqtSignal(str, str)   # (contact, kind) — incoming WhatsApp
    _call_in_hide_sig = pyqtSignal()
    _plan_show_sig   = pyqtSignal(str, object)     # (goal, steps) — agent task plan
    _plan_step_sig   = pyqtSignal(int, str, str)   # (idx, status, note)
    _plan_finish_sig = pyqtSignal(str, bool)       # (summary, ok)
    _plan_hide_sig   = pyqtSignal()
    _share_sig       = pyqtSignal(bool)            # live screen share on/off

    def __init__(self, face_path: str):
        super().__init__()
        self._face_path = face_path

        # Load customization from config
        _cfg = _read_full_config()
        self._assistant_name: str = (_cfg.get("assistant_name") or "FRIDAY").strip()
        _display = self._assistant_name.upper()

        # Kayıtlı UI rengini panel/stylesheet'ler kurulmadan ÖNCE uygula
        _ui_color = (_cfg.get("ui_color") or "").strip()
        if _ui_color and _ui_color.lower() != DEFAULT_UI_COLOR:
            apply_ui_accent(_ui_color)

        self.setWindowTitle(f"{_display} — Friday 4.0")
        self.setStyleSheet(f"QMainWindow {{ background: {C.BG}; }}")
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(_DEFAULT_W, _DEFAULT_H)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width()  - _DEFAULT_W) // 2,
            (screen.height() - _DEFAULT_H) // 2,
        )

        self.on_text_command   = None
        self.on_remote_clicked = None   # callable: () -> (url, key) | None
        self.on_interrupt      = None   # callable: () -> None — stop FRIDAY mid-speech
        self.on_screen_share_stop = None  # callable: () -> None — HUD live-share STOP
        self.on_voice_change   = None   # callable: () -> None — rebuild session with new voice
        self.on_audio_device_change = None  # callable: () -> None — reopen audio streams
        self._confirm_overlay  = None   # live ConfirmBanner / outbound CallCard
        self._call_overlay     = None   # live incoming CallCard
        self._plan_overlay     = None   # live PlanPanel, if one is on screen
        self.on_plan_answer    = None   # callable(bool), set by FridayLive → AgentRunner
        self.get_plugins       = None   # callable: () -> list[dict], set by FridayLive
        self._muted            = False
        self._current_file: str | None = None
        self._remote_overlay: RemoteKeyOverlay | None = None
        self._customize_overlay: CustomizeOverlay | None = None

        central = GlassRoot()
        central.setStyleSheet("background: transparent;")
        self.setCentralWidget(central)

        self._presence_orb = FridayPresenceOrb(self)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self._left_panel = self._build_left_panel()
        body.addWidget(self._left_panel, stretch=0)

        # Center column: HUD + resizable content panel via QSplitter
        self.hud = HudCanvas(face_path, _display)
        self.hud.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._content_panel = self._build_content_panel()

        # Live camera container — replaces HUD when camera stream is active
        _cam_cont = QWidget()
        _cam_cont.setStyleSheet(f"background: transparent; {_glass_ss(10)}")
        _cam_v = QVBoxLayout(_cam_cont)
        _cam_v.setContentsMargins(0, 0, 0, 0)
        _cam_v.setSpacing(0)
        _cam_hdr = QHBoxLayout()
        _cam_hdr.setContentsMargins(8, 5, 8, 5)
        _cam_title = QLabel("◈  CAMERA FEED")
        _cam_title.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        _cam_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        _cam_hdr.addWidget(_cam_title)
        _cam_hdr.addStretch()
        _cam_x = QPushButton("✕  CLOSE")
        _cam_x.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        _cam_x.setCursor(Qt.CursorShape.PointingHandCursor)
        _cam_x.setStyleSheet(f"""
            QPushButton {{
                color: {C.TEXT_DIM}; background: transparent;
                border: none; padding: 2px 6px;
            }}
            QPushButton:hover {{ color: {C.PRI}; }}
        """)
        _cam_x.clicked.connect(self.stop_camera_stream)
        _cam_hdr.addWidget(_cam_x)
        _cam_v.addLayout(_cam_hdr)
        self._cam_live_lbl = QLabel()
        self._cam_live_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cam_live_lbl.setStyleSheet("background: transparent;")
        self._cam_live_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        _cam_v.addWidget(self._cam_live_lbl, stretch=1)

        # Stack: 0 = animated HUD, 1 = live camera, 2 = local hands board
        self._hud_cam_stack = QStackedWidget()
        self._hud_cam_stack.setStyleSheet("background: transparent;")
        self._hud_cam_stack.addWidget(self.hud)
        self._hud_cam_stack.addWidget(_cam_cont)
        self._hands_widget = HandsBoardWidget()
        self._hands_widget._on_closed = self.stop_hands_board
        self._hud_cam_stack.addWidget(self._hands_widget)
        self._hands_open = False

        self._center_split = QSplitter(Qt.Orientation.Vertical)
        self._center_split.setStyleSheet(f"""
            QSplitter {{ background: transparent; }}
            QSplitter::handle {{
                background: {_GLASS_BORDER};
                height: 4px;
            }}
            QSplitter::handle:hover {{
                background: {_GLASS_BORDER_HI};
            }}
        """)
        self._center_split.addWidget(self._hud_cam_stack)
        self._center_split.addWidget(self._content_panel)
        self._center_split.setStretchFactor(0, 3)
        self._center_split.setStretchFactor(1, 1)
        self._center_split.setCollapsible(0, False)
        body.addWidget(self._center_split, stretch=5)

        self._right_panel = self._build_right_panel()
        body.addWidget(self._right_panel, stretch=0)

        root.addLayout(body, stretch=1)
        root.addWidget(self._build_footer())

        # Quick-access drawer (floating overlay, built after central widget layout is done)
        self._quick_drawer = self._build_quick_drawer()
        self._update_autostart_btn(self._check_autostart())
        from memory.config_manager import get_brief_enabled as _gbe
        self._update_brief_btn(_gbe())

        self._clock_tmr = QTimer(self)
        self._clock_tmr.timeout.connect(self._tick_clock)
        self._clock_tmr.start(1000)
        self._tick_clock()

        # Metrik güncelleme timer'ı
        self._metric_tmr = QTimer(self)
        self._metric_tmr.timeout.connect(self._update_metrics)
        self._metric_tmr.start(2000)
        self._update_metrics()

        self._log_sig.connect(self._log.append_log)
        self._state_sig.connect(self._apply_state)
        self._content_sig.connect(self._show_content)
        self._reconfig_sig.connect(self._show_setup)
        self._camera_sig.connect(self._show_camera_frame)
        self._confirm_sig.connect(self._show_confirm_banner)
        self._confirm_hide_sig.connect(self._hide_confirm_banner)
        self._call_in_sig.connect(self._show_incoming_call)
        self._call_in_hide_sig.connect(self._hide_incoming_call)
        self._plan_show_sig.connect(self._show_plan_panel)
        self._plan_step_sig.connect(self._update_plan_step)
        self._plan_finish_sig.connect(self._finish_plan_panel)
        self._plan_hide_sig.connect(self._hide_plan_panel)
        self._share_sig.connect(self._on_share)
        self._cam_stream_sig.connect(self._on_cam_stream)
        self._cam_frame_sig.connect(self._on_cam_frame)
        self._hands_cmd_sig.connect(self._on_hands_cmd)
        self._clipboard_sig.connect(self._show_clipboard_panel)
        self._cam_stop = threading.Event()

        # Camera preview overlay (child of central widget, positioned in resizeEvent)
        self._cam_preview = _CameraPreview(self.centralWidget())

        # Live screen-share badge (no preview — would recurse into the feed)
        self._share_badge = _ScreenShareBadge(self.centralWidget())
        self._share_badge.on_stop = lambda: (
            self.on_screen_share_stop() if callable(self.on_screen_share_stop) else None
        )

        # Clipboard panel (child of central widget, bottom-center)
        self._clipboard_panel = ClipboardPanel(self.centralWidget())
        self._clipboard_panel.action_requested.connect(self._on_clipboard_action)
        QApplication.clipboard().dataChanged.connect(self._on_clipboard_changed)

        self._overlay: SetupOverlay | None = None
        self._ready = self._check_config()
        if not self._ready:
            self._show_setup()

        sc_mute = QShortcut(QKeySequence("F4"), self)
        sc_mute.activated.connect(self._toggle_mute)
        sc_full = QShortcut(QKeySequence("F11"), self)
        sc_full.activated.connect(self._toggle_fullscreen)
        sc_intr = QShortcut(QKeySequence("Escape"), self)
        sc_intr.activated.connect(self._do_interrupt)

    def _show_camera_frame(self, img_bytes: bytes):
        """Slot — display camera preview overlay (main thread)."""
        self._cam_preview.show_frame(img_bytes)
        cw = self.centralWidget()
        pw = _CameraPreview._W
        ph = self._cam_preview.height()
        self._cam_preview.setGeometry(
            cw.width() - _RIGHT_W - pw - 12,
            cw.height() - ph - 28,
            pw, ph,
        )

    # --- Live camera stream in HUD area ------------------------------------
    def _on_cam_stream(self, start: bool) -> None:
        if start:
            self._hands_widget.stop()
            self._hands_open = False
            self._hud_cam_stack.setCurrentIndex(1)
        else:
            self._cam_live_lbl.clear()
            if not getattr(self, "_hands_open", False):
                self._hud_cam_stack.setCurrentIndex(0)

    def _on_cam_frame(self, data: bytes) -> None:
        px = QPixmap()
        px.loadFromData(data)
        if not px.isNull():
            w, h = self._cam_live_lbl.width(), self._cam_live_lbl.height()
            if w > 1 and h > 1:
                self._cam_live_lbl.setPixmap(
                    px.scaled(w, h,
                              Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
                )

    def start_camera_stream(self) -> None:
        self._cam_stop.clear()
        self._cam_stream_sig.emit(True)
        t = threading.Thread(target=self._cam_loop, daemon=True, name="cam-stream")
        t.start()

    def _cam_loop(self) -> None:
        try:
            import cv2
            # Reuse camera index detected by screen_processor (cached in api_keys.json)
            cam_idx = 0
            try:
                cam_idx = int(_read_full_config().get("camera_index", 0))
            except Exception:
                pass
            try:
                backend = cv2.CAP_DSHOW if _OS == "Windows" else cv2.CAP_ANY
            except AttributeError:
                backend = 0
            cap = cv2.VideoCapture(cam_idx, backend)
            if not cap.isOpened():
                cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                return
            # warm-up frames
            for _ in range(5):
                cap.read()
            while not self._cam_stop.wait(0.033) and cap.isOpened():
                ret, frame = cap.read()
                if ret and frame is not None:
                    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
                    self._cam_frame_sig.emit(buf.tobytes())
            cap.release()
        except Exception as e:
            print(f"[Camera] Stream error: {e}")
        finally:
            self._cam_stream_sig.emit(False)

    def stop_camera_stream(self) -> None:
        self._cam_stop.set()

    def _on_share(self, active: bool) -> None:
        self._share_badge.set_active(bool(active))
        self._position_share_badge()

    def _position_share_badge(self) -> None:
        if not getattr(self, "_share_badge", None):
            return
        cw = self.centralWidget()
        bw, bh = _ScreenShareBadge._W, _ScreenShareBadge._H
        self._share_badge.setGeometry(
            cw.width() - _RIGHT_W - bw - 12,
            66,
            bw, bh,
        )
        if self._share_badge.isVisible():
            self._share_badge.raise_()

    def _on_hands_cmd(self, action: str, payload: dict) -> None:
        payload = payload or {}
        engine = self._hands_widget.engine
        if action == "open":
            self._hands_open = True
            self.stop_camera_stream()
            self._hands_widget.start()
            self._hud_cam_stack.setCurrentIndex(2)
        elif action == "close":
            self._hands_widget.stop()
            self._hands_open = False
            if self._hud_cam_stack.currentIndex() == 2:
                self._hud_cam_stack.setCurrentIndex(0)
        elif action == "present":
            if not self._hands_open:
                self._on_hands_cmd("open", {})
            engine.present(
                str(payload.get("title") or "FRIDAY"),
                str(payload.get("body") or ""),
            )
        elif action == "add_card":
            if not self._hands_open:
                self._on_hands_cmd("open", {})
            engine.add_card(
                str(payload.get("title") or "Note"),
                str(payload.get("body") or ""),
                payload.get("x"),
                payload.get("y"),
            )
        elif action == "reset":
            if not self._hands_open:
                self._on_hands_cmd("open", {})
            engine.reset()
        elif action == "hand":
            if not self._hands_open:
                self._on_hands_cmd("open", {})
            raw = str(payload.get("image") or "").strip()
            from actions.hands_models import is_model_path
            from actions.hands_tracker import resolve_media_path
            current = ""
            if hasattr(self, "_drop_zone"):
                current = self._drop_zone.current_file() or ""
            raw = resolve_media_path(raw, current) if raw else raw
            if is_model_path(raw) or (raw or "").startswith("demo:"):
                img = raw
            else:
                path = Path(raw).expanduser() if raw else None
                img = str(path.resolve()) if path and path.is_file() else raw
            engine.hand_item(
                str(payload.get("title") or ""),
                str(payload.get("body") or ""),
                img,
            )
        elif action == "yank":
            engine.yank(str(payload.get("title") or ""))
        elif action == "hover":
            engine.hover(str(payload.get("title") or ""))
        elif action == "clear":
            engine.clear()
        elif action in ("explode", "assemble"):
            if not self._hands_open:
                self._on_hands_cmd("open", {})
            title = str(payload.get("title") or "")
            if action == "explode":
                engine.explode(title)
            else:
                engine.assemble(title)
        elif action == "pick_file":
            if not self._hands_open:
                self._on_hands_cmd("open", {})
            self._hands_widget._pick_file()
        elif action in ("add_img", "present_img", "add_model", "present_model"):
            if not self._hands_open:
                self._on_hands_cmd("open", {})
            raw = str(payload.get("image") or payload.get("src") or "").strip()
            title = str(payload.get("title") or "").strip()
            from actions.hands_models import is_model_path
            from actions.hands_tracker import resolve_media_path
            current = ""
            if hasattr(self, "_drop_zone"):
                current = self._drop_zone.current_file() or ""
            if raw or current:
                raw = resolve_media_path(raw, current)
            model_ref = (
                action in ("add_model", "present_model")
                or is_model_path(raw)
                or (raw or "").startswith("demo:")
            )
            if model_ref:
                if action in ("present_img", "present_model"):
                    engine.present_model(raw, title)
                else:
                    engine.add_model(raw, title)
                return
            path = Path(raw).expanduser() if raw else None
            if not path or not path.is_file():
                print(f"[Hands] file not found: {raw}")
                engine.toast("file not found — drop it on the glass")
                return
            resolved = str(path.resolve())
            if action == "present_img":
                engine.present_image(resolved, title)
            else:
                engine.add_image(resolved, title)

    def start_hands_board(self) -> None:
        self._hands_cmd_sig.emit("open", {})

    def stop_hands_board(self) -> None:
        self._hands_cmd_sig.emit("close", {})

    def present_hands_card(self, title: str, body: str) -> None:
        self._hands_cmd_sig.emit("present", {"title": title, "body": body})

    def add_hands_card(self, title: str, body: str) -> None:
        self._hands_cmd_sig.emit("add_card", {"title": title, "body": body})

    def add_hands_card_xy(self, title: str, body: str, x=None, y=None) -> None:
        self._hands_cmd_sig.emit("add_card", {"title": title, "body": body, "x": x, "y": y})

    def reset_hands_board(self) -> None:
        self._hands_cmd_sig.emit("reset", {})

    def hand_hands_item(self, title: str, body: str = "", image: str = "") -> None:
        self._hands_cmd_sig.emit("hand", {"title": title, "body": body, "image": image})

    def yank_hands_item(self, title: str) -> None:
        self._hands_cmd_sig.emit("yank", {"title": title})

    def hover_hands_item(self, title: str) -> None:
        self._hands_cmd_sig.emit("hover", {"title": title})

    def clear_hands_board(self) -> None:
        self._hands_cmd_sig.emit("clear", {})

    def add_hands_image(self, image: str, title: str = "") -> None:
        self._hands_cmd_sig.emit("add_img", {"image": image, "title": title})

    def present_hands_image(self, image: str, title: str = "") -> None:
        self._hands_cmd_sig.emit("present_img", {"image": image, "title": title})

    def add_hands_model(self, image: str = "", title: str = "") -> None:
        self._hands_cmd_sig.emit("add_model", {"image": image, "title": title})

    def present_hands_model(self, image: str = "", title: str = "") -> None:
        self._hands_cmd_sig.emit("present_model", {"image": image, "title": title})

    def explode_hands_model(self, title: str = "") -> None:
        self._hands_cmd_sig.emit("explode", {"title": title})

    def assemble_hands_model(self, title: str = "") -> None:
        self._hands_cmd_sig.emit("assemble", {"title": title})

    def pick_hands_file(self) -> None:
        self._hands_cmd_sig.emit("pick_file", {})

    # ------------------------------------------------------------------
    # Icon generation — arc-reactor style, rendered with Pillow
    # ------------------------------------------------------------------
    @staticmethod
    def _build_FRIDAY_icon(out_path: Path) -> bool:
        """
        Render a FRIDAY arc-reactor icon at 4× resolution and downsample
        for crisp results at all sizes. Saves a multi-res .ico to out_path.
        Returns True on success.
        """
        try:
            import math
            import PIL.Image
            import PIL.ImageDraw
            import PIL.ImageFilter
        except ImportError:
            return False

        CYAN   = (255, 102, 0)
        DIM    = (180, 70, 0)
        DARK   = (10, 4, 0)
        GLOW   = (255, 140, 40)
        WHITE  = (255, 230, 210)

        def _render(sz: int) -> PIL.Image.Image:
            S  = sz * 4                     # draw at 4× then downscale
            img = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            d   = PIL.ImageDraw.Draw(img)
            cx = cy = S // 2

            # ── filled background circle ──────────────────────────────────
            R = S // 2 - 2
            d.ellipse([cx-R, cy-R, cx+R, cy+R], fill=(*DARK, 255))

            # ── outer border ring ─────────────────────────────────────────
            lw = max(2, S // 40)
            d.ellipse([cx-R, cy-R, cx+R, cy+R],
                      outline=(*CYAN, 220), width=lw)

            # ── mid decorative ring ───────────────────────────────────────
            R2 = int(R * 0.72)
            d.ellipse([cx-R2, cy-R2, cx+R2, cy+R2],
                      outline=(*DIM, 180), width=max(1, lw // 2))

            # ── 6 radial spokes (hex bolt) ────────────────────────────────
            R_inner = int(R * 0.30)
            R_outer = int(R * 0.62)
            spoke_w = max(1, S // 80)
            for i in range(6):
                angle = math.radians(i * 60 - 30)
                x1 = cx + int(R_inner * math.cos(angle))
                y1 = cy + int(R_inner * math.sin(angle))
                x2 = cx + int(R_outer * math.cos(angle))
                y2 = cy + int(R_outer * math.sin(angle))
                d.line([x1, y1, x2, y2], fill=(*GLOW, 200), width=spoke_w)

            # ── 6 tick marks on outer ring ────────────────────────────────
            for i in range(6):
                angle = math.radians(i * 60)
                for dr in range(lw * 2):
                    rx = (R - lw - dr)
                    d.point(
                        [cx + int(rx * math.cos(angle)),
                         cy + int(rx * math.sin(angle))],
                        fill=(*WHITE, 220),
                    )

            # ── inner glowing ring ────────────────────────────────────────
            Ri = int(R * 0.26)
            d.ellipse([cx-Ri, cy-Ri, cx+Ri, cy+Ri],
                      outline=(*CYAN, 255), width=max(2, lw))

            # ── bright glow soft blur applied before core ─────────────────
            # (draw a slightly larger cyan circle on a separate layer)
            glow_layer = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            gd = PIL.ImageDraw.Draw(glow_layer)
            Rc = int(R * 0.13)
            gd.ellipse([cx-Rc*2, cy-Rc*2, cx+Rc*2, cy+Rc*2],
                       fill=(*CYAN, 110))
            glow_layer = glow_layer.filter(PIL.ImageFilter.GaussianBlur(S // 14))
            img = PIL.Image.alpha_composite(img, glow_layer)
            d   = PIL.ImageDraw.Draw(img)

            # ── core dot ──────────────────────────────────────────────────
            d.ellipse([cx-Rc, cy-Rc, cx+Rc, cy+Rc], fill=(*WHITE, 255))

            # ── downscale to target size ──────────────────────────────────
            return img.resize((sz, sz), PIL.Image.LANCZOS)

        try:
            sizes  = [256, 128, 64, 48, 32, 16]
            frames = [_render(s) for s in sizes]
            frames[0].save(
                out_path,
                format="ICO",
                append_images=frames[1:],
                sizes=[(s, s) for s in sizes],
            )
            return True
        except Exception as e:
            print(f"[Shortcut] ⚠️  Icon generation failed: {e}")
            return False

    @staticmethod
    def _create_lnk_windows(lnk: str, target: str, args: str,
                             work_dir: str, icon_loc: str) -> None:
        """
        Create a Windows .lnk shortcut WITHOUT launching PowerShell or cmd.
        Tries win32com (pywin32) first; falls back to wscript.exe + VBScript.
        wscript.exe is a GUI-mode host — it never opens a console window.
        """
        # ── Option 1: pywin32 (pure Python COM, zero subprocess) ──────────
        try:
            from win32com.client import Dispatch   # type: ignore
            sh = Dispatch("WScript.Shell")
            sc = sh.CreateShortCut(lnk)
            sc.TargetPath       = target
            sc.Arguments        = f'"{args}"'
            sc.WorkingDirectory = work_dir
            sc.Description      = "J.A.R.V.I.S AI Assistant"
            sc.IconLocation     = icon_loc
            sc.save()
            return
        except ImportError:
            pass

        # ── Option 2: wscript.exe + VBScript (always available on Windows,
        #    GUI-mode executable — never opens a console window) ────────────
        vbs = "\n".join([
            'Set ws = CreateObject("WScript.Shell")',
            f'Set sc = ws.CreateShortcut("{lnk}")',
            f'sc.TargetPath = "{target}"',
            f'sc.Arguments = Chr(34) & "{args}" & Chr(34)',
            f'sc.WorkingDirectory = "{work_dir}"',
            'sc.Description = "J.A.R.V.I.S AI Assistant"',
            f'sc.IconLocation = "{icon_loc}"',
            'sc.Save',
        ])
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix=".vbs")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(vbs)
            proc = subprocess.Popen(
                ["wscript.exe", "/nologo", tmp],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            )
            proc.wait(timeout=10)
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass

    @staticmethod
    def _get_desktop_dir() -> Path:
        """
        Resolve the user's REAL desktop directory instead of assuming
        ~/Desktop, which breaks when:
          • OneDrive "Known Folder Move" relocates the desktop
            (C:/Users/x/OneDrive/Desktop) — very common on Win 10/11;
          • the XDG desktop is localized on Linux (~/Masaüstü,
            ~/Schreibtisch, ~/Bureau, …).
        Falls back to ~/Desktop only as a last resort.
        """
        home = Path.home()
        _os = platform.system()

        if _os == "Windows":
            # ── 1) SHGetKnownFolderPath(FOLDERID_Desktop) — the canonical
            #       answer; follows OneDrive redirection. No dependencies. ──
            try:
                import ctypes
                from ctypes import wintypes

                class _GUID(ctypes.Structure):
                    _fields_ = [("Data1", wintypes.DWORD),
                                ("Data2", wintypes.WORD),
                                ("Data3", wintypes.WORD),
                                ("Data4", ctypes.c_ubyte * 8)]

                # FOLDERID_Desktop {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}
                fid = _GUID(0xB4BFCC3A, 0xDB2C, 0x424C,
                            (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9,
                                                 0x9A, 0x87, 0xC6, 0x41))
                buf = ctypes.c_wchar_p()
                if ctypes.windll.shell32.SHGetKnownFolderPath(
                        ctypes.byref(fid), 0, None, ctypes.byref(buf)) == 0:
                    p = Path(buf.value)
                    ctypes.windll.ole32.CoTaskMemFree(buf)
                    if p.is_dir():
                        return p
            except Exception:
                pass

            # ── 2) Registry: User Shell Folders (may contain %VARS%) ──────
            try:
                import winreg
                with winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion"
                        r"\Explorer\User Shell Folders") as key:
                    val, _t = winreg.QueryValueEx(key, "Desktop")
                p = Path(os.path.expandvars(val))
                if p.is_dir():
                    return p
            except Exception:
                pass

        elif _os == "Linux":
            # ── xdg-user-dir honours localized names (~/Masaüstü, …) ──────
            try:
                out = subprocess.run(["xdg-user-dir", "DESKTOP"],
                                     capture_output=True, text=True, timeout=5)
                p = Path(out.stdout.strip())
                if out.stdout.strip() and p != home and p.is_dir():
                    return p
            except Exception:
                pass
            try:
                cfg = home / ".config" / "user-dirs.dirs"
                for line in cfg.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("XDG_DESKTOP_DIR"):
                        val = line.split("=", 1)[1].strip().strip('"')
                        p = Path(val.replace("$HOME", str(home)))
                        if p != home and p.is_dir():
                            return p
            except Exception:
                pass

        # macOS: ~/Desktop is always the real path (localization is
        # display-only). Everything else lands here as a last resort.
        return home / "Desktop"

    def _create_desktop_shortcut(self):
        """
        Create a desktop shortcut on Windows / macOS / Linux.
        Never opens a terminal, console, or PowerShell window on any platform.
        """
        import stat as _stat
        script  = Path(__file__).resolve().parent / "main.py"
        python  = Path(sys.executable)
        desktop = self._get_desktop_dir()

        # Arc-reactor icon (.ico — also exported as .png for Linux/macOS)
        ico_path = Path(__file__).resolve().parent / "config" / "FRIDAY.ico"
        if not ico_path.exists():
            self._build_FRIDAY_icon(ico_path)

        try:
            _os = platform.system()

            # ── Windows ───────────────────────────────────────────────────────
            if _os == "Windows":
                pythonw  = python.parent / "pythonw.exe"
                target   = str(pythonw if pythonw.exists() else python)
                lnk      = str(desktop / "J.A.R.V.I.S.lnk")
                icon_loc = str(ico_path) if ico_path.exists() else f"{target},0"
                self._create_lnk_windows(lnk, target, str(script),
                                         str(script.parent), icon_loc)

            # ── macOS — proper .app bundle (no Terminal window) ───────────────
            elif _os == "Darwin":
                app     = desktop / "J.A.R.V.I.S.app"
                mac_dir = app / "Contents" / "MacOS"
                res_dir = app / "Contents" / "Resources"
                mac_dir.mkdir(parents=True, exist_ok=True)
                res_dir.mkdir(exist_ok=True)

                # Launcher executable (bash — runs as background process,
                # macOS does NOT open Terminal for executables inside .app bundles)
                launcher = mac_dir / "FRIDAY"
                launcher.write_text(
                    "#!/usr/bin/env bash\n"
                    f'cd "{script.parent}"\n'
                    f'exec "{python}" "{script}"\n'
                )
                launcher.chmod(launcher.stat().st_mode
                               | _stat.S_IEXEC | _stat.S_IXGRP | _stat.S_IXOTH)

                # Minimal Info.plist (required for .app recognition)
                (app / "Contents" / "Info.plist").write_text(
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                    '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                    '<plist version="1.0"><dict>\n'
                    '  <key>CFBundleExecutable</key><string>FRIDAY</string>\n'
                    '  <key>CFBundleIdentifier</key>'
                    '<string>com.FRIDAY.assistant</string>\n'
                    '  <key>CFBundleName</key><string>J.A.R.V.I.S</string>\n'
                    '  <key>CFBundlePackageType</key><string>APPL</string>\n'
                    '  <key>CFBundleVersion</key><string>1.0</string>\n'
                    '</dict></plist>\n'
                )

                # Optional: copy icon as .icns (skip silently if Pillow is missing)
                try:
                    import PIL.Image
                    icns = res_dir / "AppIcon.icns"
                    PIL.Image.open(ico_path).save(icns, format="ICNS")
                    # Inject icon reference into plist
                    plist = app / "Contents" / "Info.plist"
                    txt = plist.read_text()
                    plist.write_text(
                        txt.replace(
                            '</dict></plist>',
                            '  <key>CFBundleIconFile</key>'
                            '<string>AppIcon</string>\n</dict></plist>\n',
                        )
                    )
                except Exception:
                    pass  # icon is optional

            # ── Linux — .desktop file (Terminal=false, no console) ────────────
            else:
                # Export .ico → .png for better desktop integration
                png_path = ico_path.with_suffix(".png")
                if not png_path.exists() and ico_path.exists():
                    try:
                        import PIL.Image
                        PIL.Image.open(ico_path).resize(
                            (256, 256), PIL.Image.LANCZOS
                        ).save(png_path, format="PNG")
                    except Exception:
                        png_path = ico_path  # fallback to .ico

                icon_line = f"Icon={png_path}\n" if png_path.exists() else ""
                desk = desktop / "J.A.R.V.I.S.desktop"
                desk.write_text(
                    "[Desktop Entry]\n"
                    "Name=J.A.R.V.I.S\n"
                    f"Exec={python} {script}\n"
                    f"Path={script.parent}\n"
                    "Type=Application\n"
                    "Terminal=false\n"
                    "Categories=Utility;\n"
                    + icon_line
                )
                desk.chmod(desk.stat().st_mode | 0o755)

            self._log.append_log("SYS: Desktop shortcut created.")
        except Exception as e:
            self._log.append_log(f"ERR: Shortcut failed — {e}")

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cw = self.centralWidget()
        if self._overlay and self._overlay.isVisible():
            ow, oh = 460, 390
            self._overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._remote_overlay and self._remote_overlay.isVisible():
            ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
            self._remote_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._customize_overlay and self._customize_overlay.isVisible():
            ow, oh = CustomizeOverlay._OW, CustomizeOverlay._OH
            self._customize_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if getattr(self, "_plan_overlay", None) is not None and self._plan_overlay.isVisible():
            self._centre_overlay(self._plan_overlay)
        if getattr(self, "_confirm_overlay", None) is not None and self._confirm_overlay.isVisible():
            self._centre_overlay(self._confirm_overlay)
        if getattr(self, "_call_overlay", None) is not None and self._call_overlay.isVisible():
            self._centre_overlay(self._call_overlay)
        # Camera preview — bottom-right corner of the center/HUD area
        pw = _CameraPreview._W
        ph = self._cam_preview.height() or _CameraPreview._H
        self._cam_preview.setGeometry(
            cw.width() - _RIGHT_W - pw - 12,
            cw.height() - ph - 28,
            pw, ph,
        )
        self._position_share_badge()
        # Clipboard panel — bottom-center
        if hasattr(self, '_clipboard_panel') and self._clipboard_panel.isVisible():
            self._position_clipboard_panel()
        # Quick drawer — reposition if open
        if hasattr(self, '_quick_drawer') and self._quick_drawer.isVisible():
            self._position_quick_drawer()

    def _update_metrics(self):
        snap = _metrics.snapshot()

        # CPU
        cpu = snap["cpu"]
        self._bar_cpu.set_value(cpu, f"{cpu:.0f}%")

        # MEM
        mem = snap["mem"]
        self._bar_mem.set_value(mem, f"{mem:.0f}%")

        # NET
        net = snap["net"]
        if net < 1.0:
            net_str = f"{net*1024:.0f}KB/s"
        else:
            net_str = f"{net:.1f}MB/s"
        net_pct = min(100, net * 10)  # 10 MB/s = %100
        self._bar_net.set_value(net_pct, net_str)

        # GPU
        gpu = snap["gpu"]
        if gpu >= 0:
            self._bar_gpu.set_value(gpu, f"{gpu:.0f}%")
        else:
            self._bar_gpu.set_value(0, "N/A")

        # TMP
        tmp = snap["tmp"]
        if tmp >= 0:
            tmp_pct = min(100, (tmp / 100) * 100)
            self._bar_tmp.set_value(tmp_pct, f"{tmp:.0f}°C")
        else:
            self._bar_tmp.set_value(0, "N/A")

        try:
            boot_t  = psutil.boot_time()
            elapsed = time.time() - boot_t
            h = int(elapsed // 3600)
            m = int((elapsed % 3600) // 60)
            self._uptime_lbl.setText(f"UP  {h:02d}:{m:02d}")
        except Exception:
            self._uptime_lbl.setText("UP  --:--")

        try:
            proc_count = len(psutil.pids())
            self._proc_lbl.setText(f"PROC  {proc_count}")
        except Exception:
            self._proc_lbl.setText("PROC  --")

        # Mission-control health ring
        try:
            health = max(0.0, min(100.0, 100.0 - cpu * 0.45 - mem * 0.35))
            label  = "OPTIMAL" if health >= 85 else ("STABLE" if health >= 65 else "LOADED")
            if hasattr(self, "_status_ring"):
                self._status_ring.set_status(health, label)
        except Exception:
            pass


    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            if self.isMinimized():
                self._presence_orb.sync_state(
                    self.hud.state, self.hud.speaking, self.hud.muted)
                self._presence_orb.show_orb()
            elif self._presence_orb.isVisible():
                self._presence_orb.hide()

    def _build_header(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(58)
        w.setStyleSheet(
            f"background: {_GLASS_OVERLAY}; border-bottom: 1px solid {_GLASS_BORDER};"
        )
        lay = QHBoxLayout(w)
        lay.setContentsMargins(16, 0, 16, 0)

        lay.addWidget(_LogoBadge(30))
        lay.addSpacing(8)
        brand = QLabel("FRIDAY SYSTEM HUD  •  2026")
        brand.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        brand.setStyleSheet(f"color: {C.PRI}; background: transparent; letter-spacing: 1px;")
        lay.addWidget(brand)
        lay.addSpacing(8)
        self._drawer_btn = QPushButton("⚙")
        self._drawer_btn.setFixedSize(28, 28)
        self._drawer_btn.setFont(QFont("Segoe UI", 12))
        self._drawer_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._drawer_btn.setToolTip("Settings & Controls")
        self._drawer_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_GLASS_BG}; color: {C.TEXT_DIM};
                border: 1px solid {_GLASS_BORDER}; border-radius: 8px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI_DIM}; }}
            QPushButton:checked {{ color: {C.PRI}; border-color: {C.PRI}; background: {C.PRI_GHO}; }}
        """)
        self._drawer_btn.setCheckable(True)
        self._drawer_btn.clicked.connect(self._toggle_drawer)
        lay.addWidget(self._drawer_btn)
        lay.addStretch()

        mid = QVBoxLayout(); mid.setSpacing(1)
        _disp = self._assistant_name.upper()
        self._title_lbl = QLabel(_disp)
        self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_lbl.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        self._title_lbl.setStyleSheet(
            f"color: {C.WHITE}; background: transparent; letter-spacing: 6px;"
        )
        mid.addWidget(self._title_lbl)
        self._sub_lbl = QLabel("MISSION CONTROL  •  ALL SYSTEMS OPERATIONAL")
        self._sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sub_lbl.setFont(QFont("Segoe UI", 7))
        self._sub_lbl.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent; letter-spacing: 1px;")
        mid.addWidget(self._sub_lbl)
        lay.addLayout(mid)
        lay.addStretch()

        right_col = QVBoxLayout(); right_col.setSpacing(2)
        self._clock_lbl = QLabel("00:00:00")
        self._clock_lbl.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
        self._clock_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        self._clock_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._clock_lbl)
        self._date_lbl = QLabel("")
        self._date_lbl.setFont(QFont("Segoe UI", 7))
        self._date_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._date_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._date_lbl)
        lay.addLayout(right_col)
        return w

    def _tick_clock(self):
        self._clock_lbl.setText(time.strftime("%H:%M:%S"))
        self._date_lbl.setText(time.strftime("%a %d %b %Y"))

    def _build_left_panel(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(_LEFT_W)
        w.setStyleSheet(f"background: transparent; border-right: 1px solid {_GLASS_BORDER};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 10, 8, 10)
        lay.setSpacing(6)

        hdr = QLabel("SYSTEM OVERVIEW")
        hdr.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        hdr.setStyleSheet(
            f"color: {C.PRI}; background: transparent; "
            f"border-bottom: 1px solid {_GLASS_BORDER}; padding-bottom: 4px; letter-spacing: 1px;"
        )
        lay.addWidget(hdr)
        sub = QLabel("All systems operational")
        sub.setFont(QFont("Segoe UI", 7))
        sub.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addWidget(sub)
        lay.addSpacing(2)

        self._bar_cpu = MetricBar("CPU", C.PRI)
        self._bar_mem = MetricBar("MEM", C.ACC2)
        self._bar_net = MetricBar("NET", C.GREEN)
        self._bar_gpu = MetricBar("GPU", C.ACC)
        self._bar_tmp = MetricBar("TMP", "#ff6688")

        for bar in [self._bar_cpu, self._bar_mem, self._bar_net,
                    self._bar_gpu, self._bar_tmp]:
            lay.addWidget(bar)

        lay.addSpacing(4)

        info_panel = QWidget()
        info_panel.setStyleSheet(_glass_ss(8, heavy=True))
        ip_lay = QVBoxLayout(info_panel)
        ip_lay.setContentsMargins(6, 5, 6, 5)
        ip_lay.setSpacing(3)

        self._uptime_lbl = QLabel("UP  --:--")
        self._uptime_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._uptime_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent; border: none;")
        ip_lay.addWidget(self._uptime_lbl)

        self._proc_lbl = QLabel("PROC  --")
        self._proc_lbl.setFont(QFont("Courier New", 8))
        self._proc_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
        ip_lay.addWidget(self._proc_lbl)

        os_name = {"Windows": "WIN", "Darwin": "macOS", "Linux": "LINUX"}.get(_OS, _OS.upper())
        os_lbl = QLabel(f"OS  {os_name}")
        os_lbl.setFont(QFont("Courier New", 8))
        os_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent; border: none;")
        ip_lay.addWidget(os_lbl)

        lay.addWidget(info_panel)
        lay.addSpacing(4)

        lay.addStretch()

        for txt, col in [
            ("AI CORE\nACTIVE",  C.GREEN),
            ("SEC\nCLEARED",     C.PRI),
            ("LINK\nSECURE",     C.TEXT_DIM),
        ]:
            lbl = QLabel(txt)
            lbl.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                f"color: {col}; {_glass_ss(6)} padding: 4px;"
            )
            lay.addWidget(lbl)

        return w
    def _build_right_panel(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(_RIGHT_W)
        w.setStyleSheet(f"background: transparent; border-left: 1px solid {_GLASS_BORDER};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        def _sec(txt):
            l = QLabel(txt)
            l.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            l.setStyleSheet(
                f"color: {C.PRI}; background: transparent; letter-spacing: 0.5px;"
            )
            return l

        status_card = QWidget()
        status_card.setStyleSheet(_glass_ss(10, heavy=True))
        sc_lay = QVBoxLayout(status_card)
        sc_lay.setContentsMargins(8, 8, 8, 8)
        sc_lay.addWidget(_sec("SYSTEM STATUS"))
        self._status_ring = StatusRing()
        sc_lay.addWidget(self._status_ring)
        lay.addWidget(status_card)

        lay.addWidget(_sec("ACTIVITY LOG"))
        self._log = LogWidget()
        lay.addWidget(self._log, stretch=1)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        lay.addWidget(_sec("FILE UPLOAD"))
        self._drop_zone = FileDropZone()
        self._drop_zone.file_selected.connect(self._on_file_selected)
        lay.addWidget(self._drop_zone)

        self._file_hint = QLabel("No file loaded — drop or click above to upload")
        self._file_hint.setFont(QFont("Courier New", 7))
        self._file_hint.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._file_hint.setWordWrap(True)
        lay.addWidget(self._file_hint)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep2)

        lay.addWidget(_sec("COMMAND INPUT"))
        lay.addLayout(self._build_input_row())

        self._interrupt_btn = QPushButton("✋  INTERRUPT  [ESC]")
        self._interrupt_btn.setFixedHeight(34)
        self._interrupt_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._interrupt_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._interrupt_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255, 51, 85, 0.12); color: {C.MUTED_C};
                border: 1px solid rgba(255, 51, 85, 0.35); border-radius: 8px;
            }}
            QPushButton:hover {{
                background: rgba(255, 51, 85, 0.22); border: 1px solid rgba(255, 102, 136, 0.55);
            }}
            QPushButton:pressed {{
                background: rgba(255, 51, 85, 0.30);
            }}
        """)
        self._interrupt_btn.clicked.connect(self._do_interrupt)
        lay.addWidget(self._interrupt_btn)

        self._mute_btn = QPushButton("🎙  MICROPHONE ACTIVE")
        self._mute_btn.setFixedHeight(30)
        self._mute_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mute_btn.clicked.connect(self._toggle_mute)
        self._style_mute_btn()
        lay.addWidget(self._mute_btn)

        return w

    def _build_quick_drawer(self) -> QWidget:
        """Floating overlay panel shown when the ⚙ header button is toggled."""
        _BTN_STYLE_PRI = _glass_btn_pri_ss() + " QPushButton { text-align: left; padding: 0 8px; }"
        _BTN_STYLE_DIM = _glass_btn_dim_ss() + " QPushButton { text-align: left; padding: 0 8px; }"

        w = QWidget(self.centralWidget())
        w.setObjectName("QuickDrawer")
        w.setStyleSheet(f"""
            QWidget#QuickDrawer {{
                {_glass_ss(10, heavy=True)}
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(5)

        hdr = QLabel("◈ CONTROLS")
        hdr.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent; "
                          f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;")
        lay.addWidget(hdr)

        remote_btn = QPushButton("◉  REMOTE CONTROL")
        remote_btn.setFixedHeight(30)
        remote_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        remote_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remote_btn.setStyleSheet(_BTN_STYLE_PRI)
        remote_btn.clicked.connect(self._open_remote)
        lay.addWidget(remote_btn)

        fs_btn = QPushButton("⛶  FULLSCREEN  [F11]")
        fs_btn.setFixedHeight(26)
        fs_btn.setFont(QFont("Courier New", 7))
        fs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        fs_btn.setStyleSheet(_BTN_STYLE_DIM)
        fs_btn.clicked.connect(self._toggle_fullscreen)
        lay.addWidget(fs_btn)

        sc_btn = QPushButton("⊞  CREATE DESKTOP SHORTCUT")
        sc_btn.setFixedHeight(26)
        sc_btn.setFont(QFont("Courier New", 7))
        sc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        sc_btn.setStyleSheet(_BTN_STYLE_DIM)
        sc_btn.clicked.connect(self._create_desktop_shortcut)
        lay.addWidget(sc_btn)

        self._autostart_btn = QPushButton("◉  AUTO-START: OFF")
        self._autostart_btn.setFixedHeight(26)
        self._autostart_btn.setFont(QFont("Courier New", 7))
        self._autostart_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._autostart_btn.clicked.connect(self._toggle_autostart)
        lay.addWidget(self._autostart_btn)

        cust_btn = QPushButton("⚙  CUSTOMISE ASSISTANT")
        cust_btn.setFixedHeight(26)
        cust_btn.setFont(QFont("Courier New", 7))
        cust_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cust_btn.setStyleSheet(_BTN_STYLE_DIM)
        cust_btn.clicked.connect(self._open_customize)
        lay.addWidget(cust_btn)

        self._brief_btn = QPushButton()
        self._brief_btn.setFixedHeight(26)
        self._brief_btn.setFont(QFont("Courier New", 7))
        self._brief_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._brief_btn.clicked.connect(self._toggle_brief)
        lay.addWidget(self._brief_btn)

        audio_btn = QPushButton("🎧  AUDIO DEVICES")
        audio_btn.setFixedHeight(26)
        audio_btn.setFont(QFont("Courier New", 7))
        audio_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        audio_btn.setStyleSheet(_BTN_STYLE_DIM)
        audio_btn.clicked.connect(self._open_audio_devices)
        lay.addWidget(audio_btn)

        mem_btn = QPushButton("🧠  MEMORY")
        mem_btn.setFixedHeight(26)
        mem_btn.setFont(QFont("Courier New", 7))
        mem_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        mem_btn.setStyleSheet(_BTN_STYLE_DIM)
        mem_btn.clicked.connect(self._open_memory_panel)
        lay.addWidget(mem_btn)

        plugin_btn = QPushButton("🧩  PLUGINS")
        plugin_btn.setFixedHeight(26)
        plugin_btn.setFont(QFont("Courier New", 7))
        plugin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        plugin_btn.setStyleSheet(_BTN_STYLE_DIM)
        plugin_btn.clicked.connect(self._open_plugin_manager)
        lay.addWidget(plugin_btn)

        w.adjustSize()
        return w

    def _toggle_drawer(self, checked: bool):
        if checked:
            self._position_quick_drawer()
            self._quick_drawer.show()
            self._quick_drawer.raise_()
        else:
            self._quick_drawer.hide()

    def _position_quick_drawer(self):
        if not hasattr(self, '_quick_drawer'):
            return
        _W = 220
        self._quick_drawer.setFixedWidth(_W)
        self._quick_drawer.adjustSize()
        self._quick_drawer.setGeometry(12, 54, _W, self._quick_drawer.sizeHint().height())

    def _build_input_row(self) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(5)
        self._input = QLineEdit()
        self._input.setPlaceholderText("Type a command or question…")
        self._input.setFont(QFont("Courier New", 9))
        self._input.setFixedHeight(30)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                {_glass_input_ss()}
                padding: 3px 7px;
            }}
            QLineEdit:focus {{ border: 1px solid {_GLASS_BORDER_HI}; background: rgba(255, 102, 0, 0.10); }}
        """)
        self._input.returnPressed.connect(self._send)
        row.addWidget(self._input)

        send = QPushButton("▸")
        send.setFixedSize(30, 30)
        send.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.setStyleSheet(_glass_btn_pri_ss())
        send.clicked.connect(self._send)
        row.addWidget(send)
        return row

    def _build_content_panel(self) -> QWidget:
        """
        Collapsible panel below the HUD — shows search results, news, briefings.
        Hidden by default; appears when show_content() is called.
        """
        w = QWidget()
        w.setObjectName("ContentPanel")
        w.setStyleSheet(f"""
            QWidget#ContentPanel {{
                {_glass_ss(0).replace('border-radius: 12px;', '')}
                border-top: 1px solid {_GLASS_BORDER_HI};
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 7, 12, 8)
        lay.setSpacing(5)

        # ── header row ───────────────────────────────────────────────────────
        hdr = QHBoxLayout(); hdr.setSpacing(6)

        dot = QLabel("◈")
        dot.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        dot.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(dot)

        self._content_title_lbl = QLabel("BRIEFING")
        self._content_title_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._content_title_lbl.setStyleSheet(
            f"color: {C.PRI}; background: transparent; letter-spacing: 1px;"
        )
        hdr.addWidget(self._content_title_lbl)
        hdr.addStretch()

        self._content_ts_lbl = QLabel("")
        self._content_ts_lbl.setFont(QFont("Courier New", 7))
        self._content_ts_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr.addWidget(self._content_ts_lbl)

        dismiss = QPushButton("DISMISS  ✕")
        dismiss.setFont(QFont("Courier New", 7))
        dismiss.setFixedHeight(18)
        dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 2px; padding: 0 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        dismiss.clicked.connect(w.hide)
        hdr.addWidget(dismiss)
        lay.addLayout(hdr)

        # ── separator ─────────────────────────────────────────────────────────
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); lay.addWidget(sep)

        # ── text display ──────────────────────────────────────────────────────
        self._content_display = QTextEdit()
        self._content_display.setReadOnly(True)
        self._content_display.setFont(QFont("Courier New", 8))
        self._content_display.setMinimumHeight(60)
        self._content_display.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._content_display.setStyleSheet(f"""
            QTextEdit {{
                {_glass_input_ss()}
                padding: 6px 8px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: transparent; width: 6px; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {_GLASS_BORDER}; border-radius: 3px; min-height: 16px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0; border: none;
            }}
        """)
        lay.addWidget(self._content_display)

        return w

    def _show_content(self, title: str, text: str):
        """Slot — runs on Qt main thread. Updates and shows the content panel."""
        import time as _time
        self._content_title_lbl.setText(title.upper()[:48])
        self._content_ts_lbl.setText(_time.strftime("%H:%M:%S"))
        self._content_display.setPlainText(text)
        self._content_display.moveCursor(
            self._content_display.textCursor().MoveOperation.Start
        )
        first_show = not self._content_panel.isVisible()
        self._content_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 220, 120), 220])

    def _build_footer(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(22)
        w.setStyleSheet(f"background: transparent; border-top: 1px solid {_GLASS_BORDER};")
        lay = QHBoxLayout(w); lay.setContentsMargins(14, 0, 14, 0)

        def _fl(txt, color=C.TEXT_MED):
            l = QLabel(txt); l.setFont(QFont("Courier New", 7))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        lay.addWidget(_fl("[F4] Mute  ·  [F11] Fullscreen"))
        lay.addStretch()
        lay.addWidget(_fl("By FatihMakes", C.PRI_DIM))
        return w

    def _on_file_selected(self, path: str):
        self._current_file = path
        p    = Path(path)
        cat  = _file_category(p)
        icon, _ = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size = _fmt_size(p.stat().st_size)
        self._file_hint.setText(f"{icon}  {p.name}  ·  {size}  ·  Tell {self._assistant_name} what to do with it")
        self._log.append_log(f"FILE: {p.name} ({size}) loaded")
        if self.on_text_command:
            msg = (
                f"[FILE_UPLOADED] path={path} | name={p.name} | "
                f"type={p.suffix.lstrip('.')} | size={size} | "
                f"Briefly tell the user you can see the file '{p.name}' "
                f"({size}) has been uploaded and ask what they'd like to do with it."
            )
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def notify_phone_connected(self) -> None:
        if self._remote_overlay and self._remote_overlay.isVisible():
            self._remote_overlay.mark_connected()

    def _open_remote(self):
        if not self.on_remote_clicked:
            self._log.append_log("SYS: Dashboard not running — remote unavailable.")
            return
        result = self.on_remote_clicked()
        if not result:
            self._log.append_log("SYS: Could not generate remote key.")
            return
        url    = result[0]
        key    = result[1]
        auto   = result[2] if len(result) >= 3 else ""
        manual = result[3] if len(result) >= 4 else url
        if self._remote_overlay:
            self._remote_overlay._do_close()
        cw  = self.centralWidget()
        ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
        ov  = RemoteKeyOverlay(url, key, auto_login_url=auto, manual_url=manual,
                               expiry_secs=600, parent=cw)
        ov.set_new_key_callback(self.on_remote_clicked)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.closed.connect(lambda: setattr(self, '_remote_overlay', None))
        ov.show()
        self._remote_overlay = ov
        self._log.append_log(f"SYS: Remote key generated — manual: {manual or url}")

    # ── Auto-start ──────────────────────────────────────────────────────────────

    def _check_autostart(self) -> bool:
        """Returns True if auto-start is currently registered on this OS."""
        try:
            if _OS == "Windows":
                import winreg
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
                try:
                    winreg.QueryValueEx(key, "FRIDAY_AI")
                    return True
                except FileNotFoundError:
                    return False
                finally:
                    winreg.CloseKey(key)
            elif _OS == "Darwin":
                return (Path.home() / "Library" / "LaunchAgents"
                        / "com.FRIDAY.assistant.plist").exists()
            else:
                return (Path.home() / ".config" / "autostart" / "FRIDAY.desktop").exists()
        except Exception:
            return False

    def _toggle_autostart(self):
        currently_on = self._check_autostart()
        try:
            script = str(Path(__file__).resolve().parent / "main.py")
            if _OS == "Windows":
                import winreg
                reg = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_ALL_ACCESS)
                if currently_on:
                    winreg.DeleteValue(reg, "FRIDAY_AI")
                else:
                    pythonw = Path(sys.executable).parent / "pythonw.exe"
                    exe = str(pythonw if pythonw.exists() else sys.executable)
                    winreg.SetValueEx(reg, "FRIDAY_AI", 0, winreg.REG_SZ,
                                      f'"{exe}" "{script}"')
                winreg.CloseKey(reg)
            elif _OS == "Darwin":
                plist_dir = Path.home() / "Library" / "LaunchAgents"
                plist_dir.mkdir(parents=True, exist_ok=True)
                plist = plist_dir / "com.FRIDAY.assistant.plist"
                if currently_on:
                    plist.unlink(missing_ok=True)
                else:
                    plist.write_text(
                        '<?xml version="1.0" encoding="UTF-8"?>\n'
                        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                        '<plist version="1.0"><dict>\n'
                        '  <key>Label</key><string>com.FRIDAY.assistant</string>\n'
                        '  <key>ProgramArguments</key><array>\n'
                        f'    <string>{sys.executable}</string>\n'
                        f'    <string>{script}</string>\n'
                        '  </array>\n'
                        '  <key>RunAtLoad</key><true/>\n'
                        '</dict></plist>\n'
                    )
            else:
                desk_dir = Path.home() / ".config" / "autostart"
                desk_dir.mkdir(parents=True, exist_ok=True)
                desk = desk_dir / "FRIDAY.desktop"
                if currently_on:
                    desk.unlink(missing_ok=True)
                else:
                    desk.write_text(
                        "[Desktop Entry]\n"
                        f"Name={self._assistant_name}\n"
                        f"Exec={sys.executable} {script}\n"
                        "Type=Application\nTerminal=false\n"
                        "X-GNOME-Autostart-enabled=true\n"
                    )
            enabled = not currently_on
            self._update_autostart_btn(enabled)
            self._log.append_log(
                f"SYS: Auto-start {'enabled' if enabled else 'disabled'}.")
        except Exception as e:
            self._log.append_log(f"ERR: Auto-start failed — {e}")

    def _update_autostart_btn(self, enabled: bool):
        if not hasattr(self, '_autostart_btn'):
            return
        if enabled:
            self._autostart_btn.setText("◉  AUTO-START: ON")
            self._autostart_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            self._autostart_btn.setText("◉  AUTO-START: OFF")
            self._autostart_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    def _toggle_brief(self):
        from memory.config_manager import get_brief_enabled, save_brief_enabled
        new_val = not get_brief_enabled()
        save_brief_enabled(new_val)
        self._update_brief_btn(new_val)

    def _update_brief_btn(self, enabled: bool):
        if not hasattr(self, '_brief_btn'):
            return
        if enabled:
            self._brief_btn.setText("☀  MORNING BRIEF: ON")
            self._brief_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            self._brief_btn.setText("☀  MORNING BRIEF: OFF")
            self._brief_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    # ── Customization ────────────────────────────────────────────────────────────

    def _open_customize(self):
        cfg = _read_full_config()
        if self._customize_overlay:
            self._customize_overlay.hide()
        cw = self.centralWidget()
        ov = CustomizeOverlay(
            cfg.get("assistant_name", "FRIDAY") or "FRIDAY",
            cfg.get("user_name", ""),
            cfg.get("ui_color", "") or DEFAULT_UI_COLOR,
            cfg.get("voice_name", ""),
            parent=cw,
        )
        ow, oh = CustomizeOverlay._OW, CustomizeOverlay._OH
        oh = min(oh, cw.height() - 16)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.on_preview = self._preview_ui_color
        ov.saved.connect(self._apply_name_update)
        ov.show()
        self._customize_overlay = ov

    def _preview_ui_color(self, hex_color: str):
        """Canlı önizleme — tüm arayüzü yeni renge boyar (config'e YAZMAZ)."""
        old = current_palette()
        if apply_ui_accent(hex_color):
            retheme_all_widgets(old, current_palette())

    def _apply_name_update(self, name: str, user_name: str, ui_color: str = "",
                           voice: str = ""):
        """Update all name/theme-dependent UI elements and persist to config."""
        self._assistant_name = name.strip() or "FRIDAY"
        display = self._assistant_name.upper()
        self.setWindowTitle(f"{display} — Friday 4.0")
        self._title_lbl.setText(display)
        self._sub_lbl.setText("MISSION CONTROL  •  ALL SYSTEMS OPERATIONAL")
        self._log._ai_name_lc = self._assistant_name.lower()
        self.hud._assistant_name = display

        color_changed = False
        if ui_color:
            old = current_palette()
            if apply_ui_accent(ui_color):
                # Tüm arayüzü (paneller, butonlar, kenarlıklar, HUD) canlı boya
                retheme_all_widgets(old, current_palette())
                color_changed = old["PRI"] != C.PRI

        # Voice change → persist and, if it actually changed, rebuild the Live
        # session so the new voice takes effect (it's fixed at connect time).
        voice_changed = False
        if voice:
            from memory.config_manager import get_voice, save_voice
            if voice != get_voice():
                save_voice(voice)
                voice_changed = True

        try:
            data = _read_full_config()
            data["assistant_name"] = self._assistant_name
            data["user_name"] = user_name.strip()
            if ui_color:
                data["ui_color"] = ui_color.strip().lower()
            _write_full_config(data)
            self._log.append_log(f"SYS: Identity updated — {display}")
            if color_changed:
                self._log.append_log(f"SYS: UI colour applied — {ui_color}")
            if voice_changed:
                self._log.append_log(f"SYS: Voice set — {voice}")
        except Exception as e:
            self._log.append_log(f"ERR: Config save failed — {e}")

        if voice_changed and self.on_voice_change:
            self.on_voice_change()

    def _centre_overlay(self, ov) -> None:
        """Place a floating overlay in the middle of the HUD and show it."""
        cw = self.centralWidget()
        ov.adjustSize()
        ov.setGeometry(
            max(0, (cw.width()  - ov.width())  // 2),
            max(0, (cw.height() - ov.height()) // 2),
            ov.width(), ov.height(),
        )
        ov.show()
        ov.raise_()

    # ── Audio devices ────────────────────────────────────────────────────────

    def _open_audio_devices(self):
        ov = AudioDeviceOverlay(parent=self.centralWidget())
        ov.picked.connect(self._on_audio_devices_applied)
        self._centre_overlay(ov)
        self._audio_overlay = ov            # keep a reference so it isn't GC'd

    def _on_audio_devices_applied(self):
        self._log.append_log("SYS: Audio devices updated.")
        if self.on_audio_device_change:
            self.on_audio_device_change()

    # ── Memory panel ─────────────────────────────────────────────────────────

    def _open_memory_panel(self):
        ov = MemoryOverlay(parent=self.centralWidget())
        self._centre_overlay(ov)
        self._memory_overlay = ov

    # ── Irreversible-action confirmation ─────────────────────────────────────

    def _show_confirm_banner(self, title: str, detail: str):
        self._hide_confirm_banner()
        parsed = None
        try:
            from plugins.whatsapp_call import parse_banner
            parsed = parse_banner(title, detail)
        except Exception:
            parsed = None
        if parsed:
            contact, kind = parsed
            ov = CallCard(contact, kind, inbound=False, parent=self.centralWidget())
            ov.action.connect(lambda a: self._on_confirm_answered(a == "call"))
        else:
            ov = ConfirmBanner(title, detail, parent=self.centralWidget())
            ov.answered.connect(self._on_confirm_answered)
        self._centre_overlay(ov)
        self._confirm_overlay = ov

    def _hide_confirm_banner(self):
        ov = getattr(self, "_confirm_overlay", None)
        if ov is not None:
            ov.hide()
            ov.deleteLater()
            self._confirm_overlay = None

    def _on_confirm_answered(self, accepted: bool):
        # Tear the banner down first: core.confirm.resolve() may be about to
        # shut the machine down, and a live widget mid-callback is not where you
        # want to be when that happens.
        self._hide_confirm_banner()
        try:
            from core.confirm import resolve
            resolve(bool(accepted))
        except Exception as e:
            self._log.append_log(f"ERR: Confirmation failed — {e}")

    def _show_incoming_call(self, contact: str, kind: str):
        self._hide_incoming_call()
        ov = CallCard(contact, kind, inbound=True, parent=self.centralWidget())
        ov.action.connect(self._on_incoming_call_action)
        self._centre_overlay(ov)
        self._call_overlay = ov
        self._log.append_log(f"SYS: Incoming WhatsApp {kind} — {contact}")

    def _hide_incoming_call(self):
        ov = getattr(self, "_call_overlay", None)
        if ov is not None:
            ov.hide()
            ov.deleteLater()
            self._call_overlay = None

    def _on_incoming_call_action(self, action: str):
        self._hide_incoming_call()
        act = (action or "").strip().lower()

        def _worker():
            try:
                from actions._whatsapp_ui import (
                    accept_call, decline_call, decline_with_message,
                )
                if act == "accept":
                    msg = accept_call()
                elif act == "auto_reply":
                    msg = decline_with_message("", "")
                else:
                    msg = decline_call()
                self._log_sig.emit(f"SYS: {msg}")
            except Exception as e:
                self._log_sig.emit(f"ERR: Incoming call action failed — {e}")

        threading.Thread(target=_worker, daemon=True, name="wa-incoming").start()

    # ── Agent task plan panel ────────────────────────────────────────────────

    def _show_plan_panel(self, goal: str, steps: list):
        self._hide_plan_panel()
        ov = PlanPanel(goal, steps, parent=self.centralWidget())
        ov.answered.connect(self._on_plan_answered)
        ov.closed.connect(self._hide_plan_panel)
        self._centre_overlay(ov)
        self._plan_overlay = ov

    def _on_plan_answered(self, accepted: bool):
        ov = self._plan_overlay
        if ov is not None and not ov.executing and accepted:
            # Flip the panel into its live-checklist mode before execution
            # starts, so the first step's ▸ lands on a panel that already
            # shows STOP instead of APPROVE.
            ov.enter_execution()
        if self.on_plan_answer:
            try:
                self.on_plan_answer(bool(accepted))
            except Exception as e:
                self._log.append_log(f"ERR: Plan answer failed — {e}")

    def _update_plan_step(self, idx: int, status: str, note: str):
        if self._plan_overlay is not None:
            self._plan_overlay.set_step(idx, status, note)
            self._centre_overlay(self._plan_overlay)

    def _finish_plan_panel(self, summary: str, ok: bool):
        if self._plan_overlay is not None:
            self._plan_overlay.finish(summary, ok)
            self._centre_overlay(self._plan_overlay)

    def _hide_plan_panel(self):
        ov = self._plan_overlay
        if ov is not None:
            ov.hide()
            ov.deleteLater()
            self._plan_overlay = None

    def _open_plugin_manager(self):
        plugins = self.get_plugins() if self.get_plugins else []
        cw = self.centralWidget()
        ov = PluginManagerOverlay(plugins, parent=cw)
        ov.adjustSize()
        ov.setGeometry(
            (cw.width()  - ov.width())  // 2,
            (cw.height() - ov.height()) // 2,
            ov.width(), ov.height(),
        )
        ov.show()
        ov.raise_()
        self._plugin_manager_overlay = ov   # keep a reference so it isn't GC'd

    # ── Clipboard intelligence ───────────────────────────────────────────────────

    def _on_clipboard_changed(self):
        try:
            text = QApplication.clipboard().text().strip()
            if len(text) >= 10:
                self._clipboard_sig.emit(text)
        except Exception:
            pass

    def _show_clipboard_panel(self, text: str):
        self._clipboard_panel.show_clipboard(text)
        self._position_clipboard_panel()

    def _position_clipboard_panel(self):
        cw = self.centralWidget()
        pw = ClipboardPanel._W
        ph = self._clipboard_panel.sizeHint().height() or ClipboardPanel._H
        x = (cw.width() - pw) // 2
        y = cw.height() - ph - 6
        self._clipboard_panel.setGeometry(x, y, pw, ph)
        self._clipboard_panel.raise_()

    def _on_clipboard_action(self, cmd: str):
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(cmd,), daemon=True).start()

    # ────────────────────────────────────────────────────────────────────────────

    def _do_interrupt(self):
        if self.on_interrupt:
            self.on_interrupt()

    def _toggle_mute(self):
        self._muted = not self._muted
        self.hud.muted = self._muted
        self._style_mute_btn()
        if self._muted:
            self._apply_state("MUTED")
            self._log.append_log("SYS: Microphone muted.")
        else:
            self._apply_state("LISTENING")
            self._log.append_log("SYS: Microphone active.")

    def _style_mute_btn(self):
        if self._muted:
            self._mute_btn.setText("🔇  MICROPHONE MUTED")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(255, 51, 85, 0.12); color: {C.MUTED_C};
                    border: 1px solid rgba(255, 51, 85, 0.35); border-radius: 8px;
                }}
            """)
        else:
            self._mute_btn.setText("🎙  MICROPHONE ACTIVE")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(0, 255, 136, 0.10); color: {C.GREEN};
                    border: 1px solid rgba(0, 255, 136, 0.35); border-radius: 8px;
                }}
                QPushButton:hover {{ background: rgba(0, 255, 136, 0.18); }}
            """)

    def _send(self):
        txt = self._input.text().strip()
        if not txt: return
        self._input.clear()
        self._log.append_log(f"You: {txt}")
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(txt,), daemon=True).start()

    def _apply_state(self, state: str):
        self.hud.state    = state
        self.hud.speaking = (state == "SPEAKING")
        self.hud.muted    = self._muted
        self._presence_orb.sync_state(state, state == "SPEAKING", self._muted)
        if self._presence_orb.isVisible():
            self._presence_orb.update()
        if getattr(self, "_hands_open", False):
            try:
                self._hands_widget.engine.set_ring_state(state)
            except Exception:
                pass

    def _check_config(self) -> bool:
        if not API_FILE.exists(): return False
        try:
            d = _read_full_config()
            return bool(d.get("gemini_api_key")) and bool(d.get("os_system"))
        except Exception:
            return False

    def _show_setup(self):
        ov = SetupOverlay(self.centralWidget())
        cw = self.centralWidget()
        ow, oh = 460, 390
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.done.connect(self._on_setup_done)
        ov.show()
        self._overlay = ov

    def _on_setup_done(self, key: str, os_name: str):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        data = _read_full_config()
        data["gemini_api_key"] = key
        data["os_system"] = os_name
        _write_full_config(data)
        self._ready = True
        if self._overlay:
            self._overlay.hide()
            self._overlay = None
        self._apply_state("LISTENING")
        self._assistant_name = _read_full_config().get("assistant_name", "FRIDAY") or "FRIDAY"
        self._log.append_log(f"SYS: Initialised. OS={os_name.upper()}. {self._assistant_name} online.")

class _RootShim:
    def __init__(self, app: QApplication):
        self._app = app
    def mainloop(self):
        self._app.exec()
    def protocol(self, *_):
        pass


class FridayUI:
    def __init__(self, face_path: str, size=None):
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        self._app.setAttribute(Qt.ApplicationAttribute.AA_UseStyleSheetPropagationInWidgetStyles, True)
        self._win = MainWindow(face_path)
        self._win.show()
        self.root = _RootShim(self._app)

    @property
    def muted(self) -> bool:
        return self._win._muted

    @muted.setter
    def muted(self, v: bool):
        if v != self._win._muted:
            self._win._toggle_mute()

    @property
    def current_file(self) -> str | None:
        return self._win._drop_zone.current_file()

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

    @property
    def on_remote_clicked(self):
        return self._win.on_remote_clicked

    @on_remote_clicked.setter
    def on_remote_clicked(self, cb):
        self._win.on_remote_clicked = cb

    @property
    def on_interrupt(self):
        return self._win.on_interrupt

    @on_interrupt.setter
    def on_interrupt(self, cb):
        self._win.on_interrupt = cb

    @property
    def on_screen_share_stop(self):
        return self._win.on_screen_share_stop

    @on_screen_share_stop.setter
    def on_screen_share_stop(self, cb):
        self._win.on_screen_share_stop = cb

    @property
    def on_voice_change(self):
        return self._win.on_voice_change

    @on_voice_change.setter
    def on_voice_change(self, cb):
        self._win.on_voice_change = cb

    @property
    def on_audio_device_change(self):
        return self._win.on_audio_device_change

    @on_audio_device_change.setter
    def on_audio_device_change(self, cb):
        self._win.on_audio_device_change = cb

    def show_confirm(self, title: str, detail: str) -> None:
        """Thread-safe: raise the irreversible-action gate. Called from action
        handlers running in executor threads, so it goes through a signal."""
        self._win._confirm_sig.emit(str(title)[:120], str(detail)[:300])

    def hide_confirm(self) -> None:
        """Thread-safe: take the gate down."""
        self._win._confirm_hide_sig.emit()

    def show_incoming_call(self, contact: str, kind: str = "voice") -> None:
        """Thread-safe: raise the incoming WhatsApp call card."""
        self._win._call_in_sig.emit(str(contact or "WhatsApp")[:80], str(kind or "voice")[:16])

    def hide_incoming_call(self) -> None:
        self._win._call_in_hide_sig.emit()

    # ── Agent task plan (all thread-safe via signals) ─────────────────────────

    def show_plan(self, goal: str, steps: list) -> None:
        """Raise the plan panel: steps = [{'title','tool','destructive'}, …]."""
        self._win._plan_show_sig.emit(str(goal), list(steps))

    def update_plan_step(self, idx: int, status: str, note: str = "") -> None:
        self._win._plan_step_sig.emit(int(idx), str(status), str(note or ""))

    def finish_plan(self, summary: str, ok: bool) -> None:
        self._win._plan_finish_sig.emit(str(summary), bool(ok))

    def hide_plan(self) -> None:
        self._win._plan_hide_sig.emit()

    @property
    def on_plan_answer(self):
        return self._win.on_plan_answer

    @on_plan_answer.setter
    def on_plan_answer(self, cb):
        self._win.on_plan_answer = cb

    @property
    def get_plugins(self):
        return self._win.get_plugins

    @get_plugins.setter
    def get_plugins(self, cb):
        self._win.get_plugins = cb

    def set_audio_level(self, level: float) -> None:
        """Thread-safe: feed a 0.0–1.0 live audio level to the HUD waveform.
        Called from the audio threads; a plain float store is atomic under the
        GIL, so no signal/lock is needed for this cosmetic value."""
        try:
            self._win.hud.set_audio_level(level)
            self._win._presence_orb.set_audio_level(level)
        except Exception:
            pass

    def notify_phone_connected(self) -> None:
        self._win.notify_phone_connected()

    def set_state(self, state: str):
        self._win._state_sig.emit(state)

    def write_log(self, text: str):
        self._win._log_sig.emit(text)

    def wait_for_api_key(self):
        while not self._win._ready:
            time.sleep(0.1)

    def show_content(self, title: str, text: str):
        """Thread-safe: display content in the panel below the HUD."""
        self._win._content_sig.emit(title[:48], text[:4000])

    def prompt_reconfig(self):
        """Thread-safe: show the API key setup overlay (e.g. after an auth error)."""
        self._win._ready = False
        self._win._reconfig_sig.emit()

    def show_camera_frame(self, img_bytes: bytes):
        """Thread-safe: show a webcam frame in the small overlay (screen captures)."""
        self._win._camera_sig.emit(img_bytes)

    def start_camera_stream(self) -> None:
        """Thread-safe: start live camera feed in the full HUD area."""
        self._win.start_camera_stream()

    def stop_camera_stream(self) -> None:
        """Thread-safe: stop the live camera feed."""
        self._win.stop_camera_stream()

    def start_hands_board(self) -> None:
        self._win.start_hands_board()

    def stop_hands_board(self) -> None:
        self._win.stop_hands_board()

    def present_hands_card(self, title: str, body: str) -> None:
        self._win.present_hands_card(title, body)

    def add_hands_card(self, title: str, body: str) -> None:
        self._win.add_hands_card(title, body)

    def add_hands_card_xy(self, title: str, body: str, x=None, y=None) -> None:
        self._win.add_hands_card_xy(title, body, x, y)

    def reset_hands_board(self) -> None:
        self._win.reset_hands_board()

    def hand_hands_item(self, title: str, body: str = "", image: str = "") -> None:
        self._win.hand_hands_item(title, body, image)

    def yank_hands_item(self, title: str) -> None:
        self._win.yank_hands_item(title)

    def hover_hands_item(self, title: str) -> None:
        self._win.hover_hands_item(title)

    def clear_hands_board(self) -> None:
        self._win.clear_hands_board()

    def add_hands_image(self, image: str, title: str = "") -> None:
        self._win.add_hands_image(image, title)

    def present_hands_image(self, image: str, title: str = "") -> None:
        self._win.present_hands_image(image, title)

    def add_hands_model(self, image: str = "", title: str = "") -> None:
        self._win.add_hands_model(image, title)

    def present_hands_model(self, image: str = "", title: str = "") -> None:
        self._win.present_hands_model(image, title)

    def explode_hands_model(self, title: str = "") -> None:
        self._win.explode_hands_model(title)

    def assemble_hands_model(self, title: str = "") -> None:
        self._win.assemble_hands_model(title)

    def pick_hands_file(self) -> None:
        self._win.pick_hands_file()

    def hands_board_open(self) -> bool:
        return bool(getattr(self._win, "_hands_open", False))

    def set_screen_share(self, active: bool) -> None:
        """Thread-safe: show or hide the LIVE SCREEN badge."""
        self._win._share_sig.emit(bool(active))

    @property
    def assistant_name(self) -> str:
        return self._win._assistant_name

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.muted:
            self.set_state("LISTENING")