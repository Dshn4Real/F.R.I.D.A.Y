"""Offscreen GPU hologram for the hands board.

Renders cyan wireframes to an FBO and returns a QImage. The camera stays on
the normal QLabel — this never sits on top of the webcam view.
"""

from __future__ import annotations

from PyQt6.QtGui import QImage, QOffscreenSurface, QOpenGLContext, QSurfaceFormat, QVector2D, QVector3D, QVector4D
from PyQt6.QtOpenGL import (
    QOpenGLBuffer,
    QOpenGLFramebufferObject,
    QOpenGLFramebufferObjectFormat,
    QOpenGLFunctions_2_1,
    QOpenGLShader,
    QOpenGLShaderProgram,
)
from PyQt6 import sip
import numpy as np

GL_COLOR_BUFFER_BIT = 0x00004000
GL_DEPTH_BUFFER_BIT = 0x00000100
GL_DEPTH_TEST = 0x0B71
GL_BLEND = 0x0BE2
GL_SRC_ALPHA = 0x0302
GL_ONE_MINUS_SRC_ALPHA = 0x0303
GL_FLOAT = 0x1406
GL_UNSIGNED_INT = 0x1405
GL_LINES = 0x0001
GL_LINE_SMOOTH = 0x0B20
GL_LINE_SMOOTH_HINT = 0x0C52
GL_NICEST = 0x1102
GL_LEQUAL = 0x0203
GL_RGBA8 = 0x8058

_MESH_VERT = """
#version 120
attribute vec3 aPos;
attribute vec3 aDir;
uniform vec3 uR0;
uniform vec3 uR1;
uniform vec3 uR2;
uniform float uEx;
uniform vec2 uCenter;
uniform float uRadius;
uniform vec2 uView;
void main() {
    vec3 p = aPos + aDir * (0.85 * uEx);
    vec3 cam = vec3(dot(uR0, p), dot(uR1, p), dot(uR2, p));
    float z = cam.z * 0.42 + 2.15;
    if (z < 0.35) z = 0.35;
    vec2 pix = vec2(uCenter.x + cam.x / z * uRadius,
                    uCenter.y - cam.y / z * uRadius);
    vec2 ndc = vec2(pix.x / uView.x * 2.0 - 1.0,
                    1.0 - pix.y / uView.y * 2.0);
    float ndc_z = clamp(cam.z * 0.18 + 0.35, 0.02, 0.98);
    gl_Position = vec4(ndc, ndc_z, 1.0);
}
"""

_MESH_FRAG = """
#version 120
uniform vec4 uColor;
void main() {
    gl_FragColor = uColor;
}
"""


def _fill_buffer(buf: QOpenGLBuffer, raw: bytes) -> None:
    arr = np.frombuffer(raw, dtype=np.uint8).copy()
    buf._keep = arr
    ptr = sip.voidptr(arr)
    buf.allocate(ptr, int(arr.nbytes))


def _format() -> QSurfaceFormat:
    fmt = QSurfaceFormat()
    fmt.setAlphaBufferSize(8)
    fmt.setDepthBufferSize(24)
    fmt.setStencilBufferSize(0)
    fmt.setVersion(2, 1)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CompatibilityProfile)
    return fmt


class _MeshGPU:
    __slots__ = ("pos", "dr", "idx", "count")

    def __init__(self):
        self.pos = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.dr = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.idx = QOpenGLBuffer(QOpenGLBuffer.Type.IndexBuffer)
        self.count = 0

    def destroy(self) -> None:
        for buf in (self.pos, self.dr, self.idx):
            if buf.isCreated():
                buf.destroy()


class HandsGLRenderer:
    """Lazy offscreen GL context. Safe to construct with no camera widget."""

    def __init__(self):
        self.failed = False
        self._ctx: QOpenGLContext | None = None
        self._surf: QOffscreenSurface | None = None
        self._gl: QOpenGLFunctions_2_1 | None = None
        self._prog: QOpenGLShaderProgram | None = None
        self._fbo: QOpenGLFramebufferObject | None = None
        self._fbo_wh = (0, 0)
        self._meshes: dict[str, _MeshGPU] = {}

    def start(self) -> bool:
        if self.failed:
            return False
        if self._ctx is not None:
            return True
        try:
            fmt = _format()
            surf = QOffscreenSurface()
            surf.setFormat(fmt)
            surf.create()
            if not surf.isValid():
                raise RuntimeError("offscreen surface failed")
            ctx = QOpenGLContext()
            ctx.setFormat(fmt)
            if not ctx.create():
                raise RuntimeError("OpenGL context failed")
            if not ctx.makeCurrent(surf):
                raise RuntimeError("makeCurrent failed")
            gl = QOpenGLFunctions_2_1()
            if not gl.initializeOpenGLFunctions():
                raise RuntimeError("OpenGL 2.1 is not available")
            gl.glEnable(GL_LINE_SMOOTH)
            gl.glHint(GL_LINE_SMOOTH_HINT, GL_NICEST)
            gl.glEnable(GL_BLEND)
            gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            gl.glDepthFunc(GL_LEQUAL)
            prog = QOpenGLShaderProgram()
            if not prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, _MESH_VERT):
                raise RuntimeError(prog.log())
            if not prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, _MESH_FRAG):
                raise RuntimeError(prog.log())
            prog.bindAttributeLocation("aPos", 0)
            prog.bindAttributeLocation("aDir", 1)
            if not prog.link():
                raise RuntimeError(prog.log())
            ctx.doneCurrent()
            self._surf = surf
            self._ctx = ctx
            self._gl = gl
            self._prog = prog
            return True
        except Exception as e:
            self.failed = True
            self.close()
            print(f"[Hands] GPU models unavailable ({e})")
            return False

    def close(self) -> None:
        ctx, surf = self._ctx, self._surf
        if ctx is not None and surf is not None:
            try:
                ctx.makeCurrent(surf)
                for mesh in self._meshes.values():
                    mesh.destroy()
                self._meshes.clear()
                self._fbo = None
                ctx.doneCurrent()
            except Exception:
                pass
        self._ctx = None
        self._surf = None
        self._gl = None
        self._prog = None
        self._fbo = None
        self._fbo_wh = (0, 0)

    def render(self, pw: int, ph: int, iw: int, ih: int, board) -> QImage | None:
        """Draw holograms into a transparent image the size of the camera pixmap."""
        if pw < 2 or ph < 2 or iw < 2 or ih < 2:
            return None
        if not self.start() or self._ctx is None or self._surf is None or self._gl is None:
            return None
        cards = [c for c in board.snapshot() if c.kind == "model"]
        if not cards:
            return None
        self._ctx.makeCurrent(self._surf)
        try:
            self._ensure_fbo(pw, ph)
            fbo = self._fbo
            gl = self._gl
            if fbo is None:
                return None
            fbo.bind()
            gl.glViewport(0, 0, pw, ph)
            gl.glClearColor(0.0, 0.0, 0.0, 0.0)
            gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            gl.glEnable(GL_DEPTH_TEST)
            gl.glEnable(GL_BLEND)
            self._draw_models(gl, pw, ph, iw, ih, board, cards)
            img = fbo.toImage()
            fbo.release()
            return img
        except Exception as e:
            print(f"[Hands] GPU paint failed ({e})")
            self.failed = True
            return None
        finally:
            self._ctx.doneCurrent()

    def _ensure_fbo(self, w: int, h: int) -> None:
        if self._fbo is not None and self._fbo_wh == (w, h):
            return
        fmt = QOpenGLFramebufferObjectFormat()
        fmt.setAttachment(QOpenGLFramebufferObject.Attachment.CombinedDepthStencil)
        fmt.setInternalTextureFormat(GL_RGBA8)
        self._fbo = QOpenGLFramebufferObject(w, h, fmt)
        self._fbo_wh = (w, h)

    def _draw_models(self, gl, pw: int, ph: int, iw: int, ih: int, board, cards) -> None:
        from actions.hands_models import demo_engine, ensure_gpu, load_model, rot_matrix
        from actions.hands_tracker import _card_rect

        prog = self._prog
        if prog is None:
            return
        sx, sy = pw / iw, ph / ih
        live = set()
        spotlight = any(c.presented for c in board.snapshot())
        for card in cards:
            key = card.model or "demo://engine"
            live.add(key)
            gpu = self._mesh_for(key, load_model, demo_engine, ensure_gpu)
            if gpu is None or gpu.count < 2:
                continue
            x1, y1, x2, y2 = _card_rect(card, iw, ih)
            cx = (x1 + x2) * 0.5 * sx
            cy = (y1 + y2) * 0.5 * sy
            radius = max(28.0, min(x2 - x1, y2 - y1) * 0.48 * min(sx, sy))
            dim = spotlight and not card.presented
            if dim:
                color = QVector4D(0.0, 0.31, 0.35, 0.35)
            elif card.holo:
                color = QVector4D(0.0, 1.0, 0.82, 0.92)
            else:
                color = QVector4D(0.16, 0.71, 1.0, 0.92)
            R = rot_matrix(card.rx, card.ry)
            prog.bind()
            prog.setUniformValue("uR0", QVector3D(float(R[0, 0]), float(R[0, 1]), float(R[0, 2])))
            prog.setUniformValue("uR1", QVector3D(float(R[1, 0]), float(R[1, 1]), float(R[1, 2])))
            prog.setUniformValue("uR2", QVector3D(float(R[2, 0]), float(R[2, 1]), float(R[2, 2])))
            prog.setUniformValue("uEx", float(card.ex))
            prog.setUniformValue("uCenter", QVector2D(cx, cy))
            prog.setUniformValue("uRadius", float(radius))
            prog.setUniformValue("uView", QVector2D(float(pw), float(ph)))
            prog.setUniformValue("uColor", color)
            gpu.pos.bind()
            prog.enableAttributeArray(0)
            prog.setAttributeBuffer(0, GL_FLOAT, 0, 3, 0)
            gpu.dr.bind()
            prog.enableAttributeArray(1)
            prog.setAttributeBuffer(1, GL_FLOAT, 0, 3, 0)
            gpu.idx.bind()
            gl.glDrawElements(GL_LINES, gpu.count, GL_UNSIGNED_INT, None)
            gpu.idx.release()
            prog.disableAttributeArray(0)
            prog.disableAttributeArray(1)
            gpu.pos.release()
            gpu.dr.release()
            prog.release()
        for key in list(self._meshes):
            if key not in live:
                self._meshes.pop(key).destroy()

    def _mesh_for(self, key: str, load_model, demo_engine, ensure_gpu) -> _MeshGPU | None:
        cached = self._meshes.get(key)
        if cached is not None:
            return cached
        try:
            model = load_model(key)
        except Exception:
            model = demo_engine()
        pos, dr, idx = ensure_gpu(model)
        if len(pos) == 0 or len(idx) < 2:
            return None
        gpu = _MeshGPU()
        for buf, arr in (
            (gpu.pos, pos),
            (gpu.dr, dr),
            (gpu.idx, idx),
        ):
            buf.setUsagePattern(QOpenGLBuffer.UsagePattern.StaticDraw)
            if not buf.create():
                gpu.destroy()
                return None
            buf.bind()
            _fill_buffer(buf, arr.tobytes())
            buf.release()
        gpu.count = int(len(idx))
        self._meshes[key] = gpu
        return gpu
