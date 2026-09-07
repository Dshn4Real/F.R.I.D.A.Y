"""Load GLB / glTF / OBJ as explode-ready mesh parts for the hands board.

Renders in OpenCV as a cyan hologram (or a shaded solid). No three.js.
Parts are per-mesh nodes so explode/assemble has something to pull apart.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

_CACHE: dict[str, "HoloModel"] = {}
_MAX_EDGES = 2800
_MODEL_EXTS = {".glb", ".gltf", ".obj"}


def is_model_path(path: str) -> bool:
    return Path(path or "").suffix.lower() in _MODEL_EXTS


def wants_holo(path: str) -> bool:
    parts = Path(path or "").as_posix().lower().split("/")
    if "models" in parts and "holo" not in parts:
        return False
    return True


@dataclass
class MeshPart:
    verts: np.ndarray
    edges: np.ndarray
    faces: np.ndarray
    centroid: np.ndarray
    direction: np.ndarray


@dataclass
class HoloModel:
    parts: list[MeshPart] = field(default_factory=list)
    path: str = ""
    explodeable: bool = False


def demo_engine() -> HoloModel:
    """Three-part 'engine' so explode works with no file on disk."""
    chunks = [
        _box(0.0, 0.0, 0.0, 1.15, 0.42, 0.55),
        _box(-0.95, 0.0, 0.05, 0.55, 0.18, 0.85),
        _box(0.95, 0.0, 0.05, 0.55, 0.18, 0.85),
        _box(0.0, 0.38, -0.05, 0.35, 0.35, 0.35),
    ]
    return _normalize_parts(chunks, path="demo://engine")


def load_model(path: str) -> HoloModel:
    raw = (path or "").strip()
    key = raw.lower() or "demo://engine"
    cached = _CACHE.get(key)
    if cached is not None and key.startswith("demo:"):
        return cached
    if key in ("", "demo://engine", "demo:engine", "demo-engine", "demo://friday", "demo:friday"):
        model = demo_engine()
        _CACHE["demo://engine"] = model
        return model
    p = Path(raw).expanduser()
    cache_key = str(p.resolve()) if p.is_file() else ""
    if cache_key and cache_key in _CACHE:
        return _CACHE[cache_key]
    if not p.is_file():
        raise FileNotFoundError(f"No 3D file at {raw}")
    ext = p.suffix.lower()
    try:
        if ext == ".obj":
            model = _load_obj(p)
        elif ext in (".glb", ".gltf"):
            model = _load_gltf(p)
        else:
            raise ValueError(f"Unsupported 3D type {ext}")
    except Exception as e:
        print(f"[Hands] model load failed ({p.name}): {e}")
        raise
    model.path = cache_key
    _CACHE[cache_key] = model
    return model


def _box(cx, cy, cz, sx, sy, sz) -> MeshPart:
    hx, hy, hz = sx / 2, sy / 2, sz / 2
    corners = np.array([
        [cx - hx, cy - hy, cz - hz],
        [cx + hx, cy - hy, cz - hz],
        [cx + hx, cy + hy, cz - hz],
        [cx - hx, cy + hy, cz - hz],
        [cx - hx, cy - hy, cz + hz],
        [cx + hx, cy - hy, cz + hz],
        [cx + hx, cy + hy, cz + hz],
        [cx - hx, cy + hy, cz + hz],
    ], dtype=np.float32)
    faces = np.array([
        [0, 1, 2], [0, 2, 3], [4, 6, 5], [4, 7, 6],
        [0, 4, 5], [0, 5, 1], [2, 6, 7], [2, 7, 3],
        [0, 3, 7], [0, 7, 4], [1, 5, 6], [1, 6, 2],
    ], dtype=np.int32)
    return MeshPart(
        verts=corners,
        edges=_edges_from_faces(faces),
        faces=faces,
        centroid=corners.mean(axis=0),
        direction=np.zeros(3, np.float32),
    )


def _edges_from_faces(faces: np.ndarray) -> np.ndarray:
    if faces.size == 0:
        return np.zeros((0, 2), np.int32)
    raw = np.vstack([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    raw.sort(axis=1)
    return np.unique(raw, axis=0)


def _normalize_parts(parts: list[MeshPart], path: str = "") -> HoloModel:
    if not parts:
        parts = [_box(0, 0, 0, 1, 1, 1)]
    all_v = np.vstack([p.verts for p in parts])
    center = all_v.mean(axis=0)
    extent = float(np.max(np.linalg.norm(all_v - center, axis=1)) or 1.0)
    out = []
    for p in parts:
        verts = (p.verts - center) / extent
        centroid = verts.mean(axis=0)
        direction = centroid.copy()
        n = float(np.linalg.norm(direction))
        if n < 1e-4:
            direction = np.array([0.0, 1.0, 0.0], np.float32)
        else:
            direction = direction / n
        out.append(MeshPart(verts, p.edges, p.faces, centroid, direction.astype(np.float32)))
    return HoloModel(parts=out, path=path, explodeable=len(out) > 1)


def _cap_edges(edges: np.ndarray) -> np.ndarray:
    if len(edges) <= _MAX_EDGES:
        return edges
    step = max(1, len(edges) // _MAX_EDGES)
    return edges[::step][:_MAX_EDGES]


def _load_obj(path: Path) -> HoloModel:
    verts: list[list[float]] = []
    faces: list[list[int]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("v "):
            bits = line.split()
            verts.append([float(bits[1]), float(bits[2]), float(bits[3])])
        elif line.startswith("f "):
            idx = []
            for tok in line.split()[1:]:
                idx.append(int(tok.split("/")[0]) - 1)
            for i in range(1, len(idx) - 1):
                faces.append([idx[0], idx[i], idx[i + 1]])
    v = np.array(verts, np.float32) if verts else np.zeros((1, 3), np.float32)
    f = np.array(faces, np.int32) if faces else np.zeros((0, 3), np.int32)
    part = MeshPart(v, _cap_edges(_edges_from_faces(f)), f, v.mean(axis=0), np.zeros(3, np.float32))
    return _normalize_parts([part], str(path))


def _load_gltf(path: Path) -> HoloModel:
    data = path.read_bytes()
    if data[:4] == b"glTF":
        json_blob, bin_blob = _split_glb(data)
        gltf = json.loads(json_blob)
        buffers = [bin_blob]
    else:
        gltf = json.loads(data.decode("utf-8"))
        buffers = []
        for buf in gltf.get("buffers") or []:
            uri = buf.get("uri") or ""
            if uri.startswith("data:"):
                import base64
                buffers.append(base64.b64decode(uri.split(",", 1)[1]))
            else:
                buffers.append((path.parent / uri).read_bytes())

    views = gltf.get("bufferViews") or []
    accessors = gltf.get("accessors") or []
    meshes = gltf.get("meshes") or []
    nodes = gltf.get("nodes") or []

    def accessor_np(idx: int) -> np.ndarray:
        acc = accessors[int(idx)]
        ncomp = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}.get(acc.get("type", "VEC3"), 3)
        count = int(acc.get("count") or 0)
        ctype = int(acc.get("componentType", 5126))
        dtype = {
            5120: np.int8, 5121: np.uint8, 5122: np.int16,
            5123: np.uint16, 5125: np.uint32, 5126: np.float32,
        }.get(ctype, np.float32)
        size = int(np.dtype(dtype).itemsize)
        bv = acc.get("bufferView")
        if bv is None or count <= 0:
            return np.zeros((count, ncomp), np.float32)
        view = views[int(bv)]
        buf = buffers[int(view.get("buffer", 0))]
        off = int(view.get("byteOffset", 0)) + int(acc.get("byteOffset", 0))
        stride = int(view.get("byteStride") or 0) or size * ncomp
        need = off + stride * (count - 1) + size * ncomp
        if need > len(buf):
            raise ValueError("accessor reads past buffer")
        if stride == size * ncomp:
            blob = buf[off:off + count * ncomp * size]
            arr = np.frombuffer(blob, dtype=dtype, count=count * ncomp)
            return np.asarray(arr, np.float32).reshape(count, ncomp)
        out = np.empty((count, ncomp), np.float32)
        row = ncomp * size
        for i in range(count):
            start = off + i * stride
            out[i] = np.frombuffer(buf[start:start + row], dtype=dtype, count=ncomp)
        return out

    def quat_mat(q) -> np.ndarray:
        x, y, z, w = [float(v) for v in q]
        return np.array([
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), 0],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), 0],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), 0],
            [0, 0, 0, 1],
        ], dtype=np.float32)

    def node_local(node: dict) -> np.ndarray:
        if "matrix" in node:
            m = np.array(node["matrix"], np.float32).reshape(4, 4, order="F")
            return m
        t = np.eye(4, dtype=np.float32)
        r = quat_mat(node.get("rotation") or [0, 0, 0, 1])
        s = np.eye(4, dtype=np.float32)
        tt = node.get("translation") or [0, 0, 0]
        ss = node.get("scale") or [1, 1, 1]
        t[:3, 3] = tt
        s[0, 0], s[1, 1], s[2, 2] = ss
        return t @ r @ s

    world = [np.eye(4, dtype=np.float32) for _ in nodes]
    children_of = {i: list(n.get("children") or []) for i, n in enumerate(nodes)}
    mentioned = {c for kids in children_of.values() for c in kids}
    roots = [i for i in range(len(nodes)) if i not in mentioned] or list(range(len(nodes)))

    def walk(i: int, parent: np.ndarray) -> None:
        if i >= len(nodes):
            return
        world[i] = parent @ node_local(nodes[i])
        for c in children_of.get(i, []):
            walk(c, world[i])

    if nodes:
        ident = np.eye(4, dtype=np.float32)
        for r in roots:
            walk(r, ident)

    parts: list[MeshPart] = []
    draco = 0

    def add_prim(prim: dict, xf: np.ndarray | None) -> None:
        nonlocal draco
        if (prim.get("extensions") or {}).get("KHR_draco_mesh_compression"):
            draco += 1
            return
        attrs = prim.get("attributes") or {}
        if "POSITION" not in attrs:
            return
        pos = accessor_np(attrs["POSITION"]).astype(np.float32)[:, :3]
        if xf is not None and len(pos):
            hom = np.hstack([pos, np.ones((len(pos), 1), np.float32)])
            pos = (hom @ xf.T)[:, :3]
        faces = np.zeros((0, 3), np.int32)
        mode = int(prim.get("mode", 4))
        if "indices" in prim:
            idx = accessor_np(prim["indices"]).astype(np.int32).reshape(-1)
            if mode == 4 and len(idx) >= 3:
                faces = idx[: len(idx) - len(idx) % 3].reshape(-1, 3)
            elif mode in (5, 6) and len(idx) >= 3:
                # triangle strip / fan → flatten a sample of triangles
                tris = []
                if mode == 5:
                    for i in range(len(idx) - 2):
                        tris.append([idx[i], idx[i + 1], idx[i + 2]])
                else:
                    for i in range(1, len(idx) - 1):
                        tris.append([idx[0], idx[i], idx[i + 1]])
                faces = np.array(tris, np.int32) if tris else faces
        elif mode == 4 and len(pos) >= 3:
            faces = np.arange(len(pos) - len(pos) % 3, dtype=np.int32).reshape(-1, 3)
        if len(faces):
            m = int(faces.max(initial=0))
            if m >= len(pos):
                faces = faces[np.all(faces < len(pos), axis=1)]
        edges = _cap_edges(_edges_from_faces(faces)) if len(faces) else _wire_fallback(pos)
        if len(pos) == 0:
            return
        parts.append(MeshPart(pos, edges, faces, pos.mean(axis=0), np.zeros(3, np.float32)))

    for ni, node in enumerate(nodes):
        mi = node.get("mesh")
        if mi is None:
            continue
        xf = world[ni] if ni < len(world) else None
        for prim in (meshes[int(mi)].get("primitives") or []):
            add_prim(prim, xf)

    if not parts:
        for mesh in meshes:
            for prim in mesh.get("primitives") or []:
                add_prim(prim, None)

    if not parts:
        why = "Draco-compressed (can't decode)" if draco else "no mesh positions"
        raise ValueError(f"GLB has {why}")

    return _normalize_parts(parts, str(path))


def _wire_fallback(pos: np.ndarray) -> np.ndarray:
    if len(pos) < 2:
        return np.zeros((0, 2), np.int32)
    step = max(1, len(pos) // 400)
    idx = np.arange(0, len(pos) - 1, step)
    return np.stack([idx, np.minimum(idx + step, len(pos) - 1)], axis=1).astype(np.int32)


def _split_glb(data: bytes) -> tuple[bytes, bytes]:
    # header: magic, version, length
    json_blob = b"{}"
    bin_blob = b""
    offset = 12
    while offset + 8 <= len(data):
        length, ctype = struct.unpack_from("<I4s", data, offset)
        offset += 8
        chunk = data[offset:offset + length]
        offset += length
        if ctype == b"JSON":
            json_blob = chunk.split(b"\x00", 1)[0].strip()
        elif ctype == b"BIN\x00":
            bin_blob = chunk
    return json_blob, bin_blob


def rot_matrix(rx: float, ry: float) -> np.ndarray:
    cx, sx = math_cos(rx), math_sin(rx)
    cy, sy = math_cos(ry), math_sin(ry)
    rxm = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]], np.float32)
    rym = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], np.float32)
    return rym @ rxm


def math_cos(a: float) -> float:
    return float(np.cos(a))


def math_sin(a: float) -> float:
    return float(np.sin(a))


def project_model(
    model: HoloModel,
    rx: float,
    ry: float,
    ex: float,
    cx: int,
    cy: int,
    radius: float,
) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Return [(xy Nx2 int, edges, faces)] in pixel space for each part."""
    R = rot_matrix(rx, ry)
    out = []
    flight = 0.85 * float(ex)
    for part in model.parts:
        pts = part.verts + part.direction * flight
        cam = pts @ R.T
        z = cam[:, 2] * 0.42 + 2.15
        z = np.clip(z, 0.35, None)
        xy = np.empty((len(cam), 2), np.int32)
        xy[:, 0] = (cx + cam[:, 0] / z * radius).astype(np.int32)
        xy[:, 1] = (cy - cam[:, 1] / z * radius).astype(np.int32)
        out.append((xy, part.edges, part.faces))
    return out
