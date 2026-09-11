"""Headless tests for the local hands_board plugin — no webcam required."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import plugins.hands_board as hb
from actions.hands_tracker import HandsBoard, PINCH_ON_FRAMES, PINCH_OFF_FRAMES, pinch_ratio
from core.plugin_loader import discover_plugins


class FakePlayer:
    def __init__(self):
        self.cam_stopped = False
        self.hands_started = False
        self.hands_stopped = False
        self.presented = []
        self.added = []
        self.cleared = False
        self.reset = False
        self.yanked = ""
        self.hovered = ""
        self.images = []
        self.models = []
        self.exploded = ""
        self.assembled = ""
        self.logs = []
        self._open = False
        self._file = None

    def current_file(self):
        return self._file

    def stop_camera_stream(self):
        self.cam_stopped = True

    def start_hands_board(self):
        self.hands_started = True
        self._open = True

    def stop_hands_board(self):
        self.hands_stopped = True
        self._open = False

    def present_hands_card(self, title, body):
        self.presented.append((title, body))

    def add_hands_card(self, title, body):
        self.added.append((title, body))

    def add_hands_card_xy(self, title, body, x=None, y=None):
        self.added.append((title, body, x, y))

    def reset_hands_board(self):
        self.cleared = True
        self.reset = True

    def hand_hands_item(self, title, body="", image=""):
        self.images.append(("hand", image, title))

    def yank_hands_item(self, title):
        self.yanked = title

    def hover_hands_item(self, title):
        self.hovered = title

    def clear_hands_board(self):
        self.cleared = True

    def add_hands_image(self, image, title=""):
        self.images.append(("add", image, title))

    def present_hands_image(self, image, title=""):
        self.images.append(("present", image, title))

    def add_hands_model(self, image="", title=""):
        self.models.append(("add", image, title))

    def present_hands_model(self, image="", title=""):
        self.models.append(("present", image, title))

    def explode_hands_model(self, title=""):
        self.exploded = title

    def assemble_hands_model(self, title=""):
        self.assembled = title

    def hands_board_open(self):
        return self._open

    def write_log(self, text: str):
        self.logs.append(text)


def _ok_pinch(x: float, y: float):
    """OK-sign pinch: index curled to thumb, other fingers arched out."""
    pts = [(x, y)] * 21
    pts[0] = (x, y + 0.20)          # wrist
    pts[5] = (x, y + 0.05)          # index mcp
    pts[9] = (x, y + 0.10)          # middle mcp (span = 0.10)
    pts[13] = (x + 0.03, y + 0.10)
    pts[17] = (x + 0.06, y + 0.11)
    pts[4] = (x - 0.012, y)         # thumb tip
    pts[8] = (x + 0.012, y)         # index tip — gap 0.024, ratio 0.24
    pts[12] = (x, y - 0.08)         # middle extended
    pts[16] = (x + 0.04, y - 0.07)
    pts[20] = (x + 0.07, y - 0.05)
    pts[6] = (x, y + 0.03)
    pts[7] = (x + 0.006, y + 0.01)
    return pts


def _open_hand(x: float, y: float):
    pts = [(x, y)] * 21
    pts[0] = (x, y + 0.20)
    pts[5] = (x - 0.02, y + 0.10)
    pts[9] = (x, y + 0.10)
    pts[13] = (x + 0.02, y + 0.10)
    pts[17] = (x + 0.05, y + 0.12)
    pts[4] = (x - 0.12, y - 0.08)
    pts[8] = (x + 0.12, y - 0.10)
    pts[12] = (x + 0.02, y - 0.12)
    pts[16] = (x + 0.06, y - 0.10)
    pts[20] = (x + 0.10, y - 0.08)
    return pts


def _fist(x: float, y: float):
    pts = [(x, y)] * 21
    pts[0] = (x, y + 0.20)
    pts[9] = (x, y + 0.10)
    for i in (4, 8, 12, 16, 20, 5, 13, 17):
        pts[i] = (x, y + 0.12)
    return pts


def _peace(x: float, y: float):
    pts = [(x, y)] * 21
    pts[0] = (x, y + 0.22)
    pts[2] = (x - 0.05, y + 0.10)
    pts[4] = (x - 0.12, y + 0.04)
    pts[5] = (x - 0.03, y + 0.08)
    pts[8] = (x - 0.04, y - 0.14)
    pts[9] = (x, y + 0.09)
    pts[12] = (x + 0.01, y - 0.14)
    pts[13] = (x + 0.03, y + 0.10)
    pts[16] = (x + 0.04, y + 0.12)
    pts[17] = (x + 0.06, y + 0.11)
    pts[20] = (x + 0.07, y + 0.13)
    return pts


def _thumbs_up(x: float, y: float):
    pts = [(x, y)] * 21
    pts[0] = (x, y + 0.20)
    pts[2] = (x - 0.02, y + 0.04)
    pts[3] = (x - 0.02, y - 0.04)
    pts[4] = (x - 0.02, y - 0.14)
    pts[5] = (x - 0.02, y + 0.08)
    pts[8] = (x - 0.02, y + 0.10)
    pts[9] = (x, y + 0.08)
    pts[12] = (x, y + 0.10)
    pts[13] = (x + 0.03, y + 0.09)
    pts[16] = (x + 0.03, y + 0.11)
    pts[17] = (x + 0.05, y + 0.10)
    pts[20] = (x + 0.05, y + 0.12)
    return pts


def test_plugin_discovers():
    reg = discover_plugins(ROOT / "plugins", set(), logger=lambda *_: None)
    assert reg.has("hands_board")
    decls = {d["name"] for d in reg.get_tool_declarations()}
    assert "hands_board" in decls


def test_unknown_action():
    msg = hb.run({"action": "teleport"})
    assert "specify action" in msg.lower()


def test_open_hooks_friday_window():
    player = FakePlayer()
    msg = hb.run({"action": "open"}, player=player)
    assert player.cam_stopped
    assert player.hands_started
    assert "pinch" in msg.lower()
    assert player.hands_board_open()


def test_present_and_clear():
    player = FakePlayer()
    hb.run({"action": "present", "title": "THE PLAN", "body": "step one"}, player=player)
    assert player.hands_started
    assert player.presented == [("THE PLAN", "step one")]
    hb.run({"action": "clear"}, player=player)
    assert player.cleared


def test_close():
    player = FakePlayer()
    hb.run({"action": "open"}, player=player)
    msg = hb.run({"action": "close"}, player=player)
    assert player.hands_stopped
    assert "closed" in msg.lower()


def test_status_without_ui():
    msg = hb.run({"action": "status"})
    assert "closed" in msg.lower()


def test_needs_friday_window():
    msg = hb.run({"action": "open"})
    assert "window" in msg.lower()


def test_pinch_grabs_nearest_card():
    board = HandsBoard()
    board.reset_starter()
    hand = _ok_pinch(0.50, 0.32)
    assert pinch_ratio(hand) < 0.38
    for _ in range(PINCH_ON_FRAMES + 1):
        board.tick([hand], 0.03)
    grabbed = [c for c in board.snapshot() if c.grabbed]
    assert len(grabbed) == 1
    assert grabbed[0].title == "PINCH"


def test_pinch_works_again_after_release():
    board = HandsBoard()
    board.reset_starter()
    for _ in range(PINCH_ON_FRAMES + 1):
        board.tick([_ok_pinch(0.50, 0.32)], 0.03)
    assert any(c.grabbed and c.title == "PINCH" for c in board.snapshot())
    for _ in range(PINCH_OFF_FRAMES + 1):
        board.tick([_open_hand(0.50, 0.32)], 0.03)
    assert not any(c.grabbed for c in board.snapshot())
    for _ in range(PINCH_ON_FRAMES + 1):
        board.tick([_ok_pinch(0.72, 0.48)], 0.03)
    grabbed = [c for c in board.snapshot() if c.grabbed]
    assert len(grabbed) == 1
    assert grabbed[0].title == "SCALE"


def test_fist_is_not_a_pinch():
    board = HandsBoard()
    board.reset_starter()
    for _ in range(5):
        board.tick([_fist(0.50, 0.32)], 0.03)
    assert not any(c.grabbed for c in board.snapshot())


def test_two_hands_scale():
    board = HandsBoard()
    board.reset_starter()
    for _ in range(4):
        board.tick([_ok_pinch(0.68, 0.48), _ok_pinch(0.76, 0.48)], 0.03)
    grabbed = [c for c in board.snapshot() if c.grabbed]
    assert grabbed and grabbed[0].title == "SCALE"
    s0 = grabbed[0].scale
    for _ in range(4):
        board.tick([_ok_pinch(0.58, 0.48), _ok_pinch(0.86, 0.48)], 0.03)
    scaled = [c for c in board.snapshot() if c.title == "SCALE"][0]
    assert scaled.scale > s0


def test_present_spotlights():
    board = HandsBoard()
    board.add_card("A", "one")
    board.add_card("B", "two")
    board.present("THE PLAN", "step one")
    cards = board.snapshot()
    lit = [c for c in cards if c.presented]
    assert len(lit) == 1
    assert lit[0].title == "THE PLAN"


def test_yank_flies_off():
    board = HandsBoard()
    board.add_card("HELLO", "x")
    board.yank("HELLO")
    for _ in range(40):
        board.tick([], 0.05)
    assert not any(c.title == "HELLO" for c in board.snapshot())


def test_plugin_yank_and_reset():
    player = FakePlayer()
    hb.run({"action": "open"}, player=player)
    hb.run({"action": "yank", "title": "HELLO"}, player=player)
    assert player.yanked == "HELLO"
    hb.run({"action": "reset"}, player=player)
    assert player.reset


def test_cycle_camera_flag():
    board = HandsBoard()
    board.cycle_camera()
    assert board.consume_cam_cycle() is True
    assert board.consume_cam_cycle() is False


def test_add_image_on_snapshot():
    board = HandsBoard()
    board.add_image(r"C:\pics\cat.png", "Cat")
    cards = board.snapshot()
    assert len(cards) == 1
    assert cards[0].title == "Cat"
    assert cards[0].image.endswith("cat.png")
    assert cards[0].image


def test_draw_blits_image():
    import tempfile
    from actions.hands_tracker import draw_board
    try:
        import cv2
        import numpy as np
    except ImportError:
        return
    p = Path(tempfile.mkdtemp()) / "red.png"
    cv2.imwrite(str(p), np.full((48, 48, 3), (0, 0, 255), np.uint8))
    board = HandsBoard()
    board.add_image(str(p), "Red")
    frame = np.zeros((540, 960, 3), np.uint8)
    draw_board(frame, board, [], {})
    card = board.snapshot()[0]
    cx, cy = int(card.x * 960), int(card.y * 540 + 10)
    _b, _g, r = frame[cy, cx]
    assert r > 80, f"expected red pixels on card, got {frame[cy, cx]}"


def test_clap_resets_with_ring():
    board = HandsBoard()
    board.reset_starter()
    assert len(board.snapshot()) >= 3
    board.tick([_open_hand(0.25, 0.50), _open_hand(0.75, 0.50)], 0.03)
    hint = board.tick([_open_hand(0.47, 0.50), _open_hand(0.53, 0.50)], 0.03)
    assert hint["clapping"]
    left = [c.kind for c in board.snapshot()]
    assert left == ["ring"]


def test_present_adds_center_card():
    board = HandsBoard()
    board.present("THE PLAN", "step one")
    cards = board.snapshot()
    assert len(cards) == 1
    assert cards[0].title == "THE PLAN"
    assert abs(cards[0].x - 0.50) < 0.01


def test_ring_word_maps_hud_state():
    from actions.hands_board_host import ring_word
    assert ring_word("SPEAKING") == "speaking"
    assert ring_word("LISTENING") == "listening"
    assert ring_word("THINKING") == "thinking"
    assert ring_word("SLEEPING") == "idle"
    assert ring_word("MUTED") == "idle"


def test_host_finds_installed_board():
    from actions.hands_board_host import installed
    # Optional: True if fullstack barehands was downloaded to Friday/barehands
    assert isinstance(installed(), bool)


def test_add_img_needs_path():
    player = FakePlayer()
    msg = hb.run({"action": "add_img"}, player=player)
    assert "drop" in msg.lower() or "desktop" in msg.lower() or "filename" in msg.lower()


def test_add_img_uses_current_file():
    player = FakePlayer()
    player._file = r"C:\pics\cat.png"
    hb.run({"action": "add_img"}, player=player)
    assert player.hands_started
    assert player.images == [("add", r"C:\pics\cat.png", "")]


def test_add_img_calls_player():
    player = FakePlayer()
    hb.run({"action": "add_img", "image": r"C:\pics\cat.png"}, player=player)
    assert player.hands_started
    assert player.images == [("add", r"C:\pics\cat.png", "")]


def test_present_with_image():
    player = FakePlayer()
    hb.run({"action": "present", "image": r"C:\pics\cat.png", "title": "Cat"}, player=player)
    assert player.images == [("present", r"C:\pics\cat.png", "Cat")]


def test_stage_image_copies_into_misc():
    import tempfile
    from actions import hands_board_host as host
    img = Path(tempfile.mkdtemp()) / "hello.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    orig_dir = host.board_dir
    board = Path(tempfile.mkdtemp())
    (board / "server.py").write_text("print('ok')", encoding="utf-8")
    (board / "stage.html").write_text("<html></html>", encoding="utf-8")
    host.board_dir = lambda: board
    try:
        src, err = host.stage_image(str(img))
        assert err == ""
        assert src == "misc/hello.png"
        assert (board / "media" / "misc" / "hello.png").is_file()
    finally:
        host.board_dir = orig_dir


def test_demo_engine_explodes():
    import numpy as np
    from actions.hands_models import demo_engine, project_model
    model = demo_engine()
    assert len(model.parts) >= 3
    assert model.explodeable
    packed = project_model(model, 0.2, 0.4, 0.0, 480, 270, 120)
    blown = project_model(model, 0.2, 0.4, 1.0, 480, 270, 120)
    assert len(packed) == len(model.parts)
    assert packed[0][0].shape[1] == 2
    c0 = packed[0][0].mean(axis=0)
    c1 = packed[-1][0].mean(axis=0)
    e0 = blown[0][0].mean(axis=0)
    e1 = blown[-1][0].mean(axis=0)
    assert float(np.linalg.norm(e0 - e1)) > float(np.linalg.norm(c0 - c1))


def test_gpu_pack_has_lines_and_explode_dirs():
    import numpy as np
    from actions.hands_models import demo_engine, ensure_gpu, gpu_pack
    model = demo_engine()
    pos, dr, idx = ensure_gpu(model)
    assert pos.shape[1] == 3
    assert pos.shape == dr.shape
    assert len(pos) >= 8
    assert idx.dtype == np.uint32
    assert len(idx) >= 6
    assert len(idx) % 2 == 0
    # parts fly along different explode axes
    packed = gpu_pack(model)
    assert not np.allclose(packed[1][0], packed[1][-1])


def test_load_obj_triangle():
    import tempfile
    from actions.hands_models import load_model
    p = Path(tempfile.mkdtemp()) / "tri.obj"
    p.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", encoding="utf-8")
    model = load_model(str(p))
    assert len(model.parts) >= 1
    assert len(model.parts[0].verts) >= 3


def _write_triangle_glb(path: Path, null_pad: bool = False) -> None:
    import json, struct
    pos = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    idx = struct.pack("<3H", 0, 1, 2)
    gltf = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(pos) + len(idx)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": 36},
            {"buffer": 0, "byteOffset": 36, "byteLength": 6},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3",
             "max": [1, 1, 0], "min": [0, 0, 0]},
            {"bufferView": 1, "componentType": 5123, "count": 3, "type": "SCALAR"},
        ],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        "nodes": [{"mesh": 0}],
        "scenes": [{"nodes": [0]}],
        "scene": 0,
    }
    js = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    while len(js) % 4:
        js += b"\x00" if null_pad else b" "
    blob = pos + idx
    while len(blob) % 4:
        blob += b"\x00"
    total = 12 + 8 + len(js) + 8 + len(blob)
    raw = b"glTF" + struct.pack("<II", 2, total)
    raw += struct.pack("<I4s", len(js), b"JSON") + js
    raw += struct.pack("<I4s", len(blob), b"BIN\x00") + blob
    path.write_bytes(raw)


def test_load_glb_triangle():
    import tempfile
    from actions.hands_models import load_model
    from actions.hands_tracker import draw_board
    folder = Path(tempfile.mkdtemp())
    p = folder / "ship.glb"
    _write_triangle_glb(p, null_pad=True)
    model = load_model(str(p))
    assert len(model.parts) >= 1
    assert len(model.parts[0].verts) >= 3
    board = HandsBoard()
    board.add_image(str(p), "Ship")
    card = board.snapshot()[0]
    assert card.kind == "model"
    assert card.model.endswith("ship.glb")
    try:
        import cv2  # noqa: F401
        import numpy as np
    except ImportError:
        return
    frame = np.zeros((540, 960, 3), np.uint8)
    draw_board(frame, board, [], {})
    assert int(frame[:, :, 1].max()) > 40


def test_resolve_nested_glb():
    import tempfile
    from actions.hands_tracker import resolve_media_path
    root = Path(tempfile.mkdtemp())
    nested = root / "props" / "holo"
    nested.mkdir(parents=True)
    p = nested / "engine.glb"
    p.write_bytes(b"x")
    hit = resolve_media_path("engine.glb", roots=[root])
    assert Path(hit).name == "engine.glb"


def test_add_model_and_explode():
    board = HandsBoard()
    board.add_model("", "ENGINE")
    cards = board.snapshot()
    assert len(cards) == 1
    assert cards[0].kind == "model"
    assert cards[0].ex == 0.0
    msg = board.explode()
    assert "engine" in msg.lower()
    assert board.snapshot()[0].ex == 1.0
    board.assemble()
    assert board.snapshot()[0].ex == 0.0


def test_add_image_routes_glb_to_model():
    board = HandsBoard()
    board.add_image(r"C:\missing\ship.glb", "Ship")
    card = board.snapshot()[0]
    assert card.kind == "model"
    assert card.title == "Ship"


def test_draw_model_wires():
    from actions.hands_tracker import draw_board
    try:
        import cv2  # noqa: F401
        import numpy as np
    except ImportError:
        return
    board = HandsBoard()
    board.add_model("", "ENGINE")
    frame = np.zeros((540, 960, 3), np.uint8)
    draw_board(frame, board, [], {})
    # hologram wires are cyan-green; overlay alone is near-black
    assert int(frame[:, :, 1].max()) > 80


def test_empty_pinch_does_not_explode():
    board = HandsBoard()
    board.add_model("", "ENGINE")
    for _ in range(PINCH_ON_FRAMES + 1):
        board.tick([_ok_pinch(0.82, 0.18)], 0.03)
    assert not any(c.grabbed for c in board.snapshot())
    for i in range(14):
        board.tick([_ok_pinch(0.82 + i * 0.01, 0.18)], 0.03)
    assert board.snapshot()[0].ex == 0.0


def test_peace_explodes_model():
    from actions.hands_gestures import POSE_HOLD_FRAMES, measure
    assert measure(_peace(0.70, 0.40)).peace
    board = HandsBoard()
    board.add_model("", "ENGINE")
    for _ in range(POSE_HOLD_FRAMES + 2):
        board.tick([_peace(0.70, 0.40)], 0.03)
    assert board.snapshot()[0].ex == 1.0


def test_thumbs_up_assembles_model():
    from actions.hands_gestures import POSE_HOLD_FRAMES, measure
    assert measure(_thumbs_up(0.70, 0.40)).thumbs
    board = HandsBoard()
    board.add_model("", "ENGINE")
    board.explode()
    assert board.snapshot()[0].ex == 1.0
    for _ in range(POSE_HOLD_FRAMES + 2):
        board.tick([_thumbs_up(0.70, 0.40)], 0.03)
    assert board.snapshot()[0].ex == 0.0


def test_plugin_add_model_and_explode():
    player = FakePlayer()
    msg = hb.run({"action": "add_model", "title": "ENGINE"}, player=player)
    assert player.hands_started
    assert player.models == [("add", "", "ENGINE")]
    assert "3d" in msg.lower() or "hologram" in msg.lower()
    hb.run({"action": "explode", "title": "ENGINE"}, player=player)
    assert player.exploded == "ENGINE"
    hb.run({"action": "assemble", "title": "ENGINE"}, player=player)
    assert player.assembled == "ENGINE"


def test_plugin_add_img_glb():
    player = FakePlayer()
    hb.run({"action": "add_img", "image": r"C:\props\ship.glb", "title": "Ship"}, player=player)
    assert player.models == [("add", r"C:\props\ship.glb", "Ship")]
    assert player.images == []


def test_resolve_media_by_name():
    import tempfile
    from actions.hands_tracker import resolve_media_path
    folder = Path(tempfile.mkdtemp())
    (folder / "glasscat.png").write_bytes(b"x")
    hit = resolve_media_path("glasscat.png", roots=[folder])
    assert hit.endswith("glasscat.png")
    assert Path(hit).is_file()


def test_ring_is_friday_model():
    from actions.hands_tracker import draw_board
    try:
        import cv2  # noqa: F401
        import numpy as np
    except ImportError:
        board = HandsBoard()
        board.reset()
        rings = [c for c in board.snapshot() if c.kind == "ring"]
        assert len(rings) == 1
        assert rings[0].title == "FRIDAY"
        return
    board = HandsBoard()
    board.reset()
    rings = [c for c in board.snapshot() if c.kind == "ring"]
    assert len(rings) == 1
    assert rings[0].title == "FRIDAY"
    assert rings[0].kind == "ring"
    frame = np.zeros((540, 960, 3), np.uint8)
    draw_board(frame, board, [], {})
    # The 3D FRIDAY orb is painted by the HUD onto this ring card.


def test_model_spin_does_not_shrink_footprint():
    from actions.hands_tracker import Card, _card_rect
    card = Card("ENGINE", "", 0.50, 0.46, 0.42, 0.42, kind="model")
    x1, y1, x2, y2 = _card_rect(card, 960, 540)
    wide = x2 - x1
    card.ry = 1.57
    card.rx = 0.9
    a1, b1, a2, b2 = _card_rect(card, 960, 540)
    assert (a2 - a1) > wide * 0.85
    assert (b2 - b1) > (y2 - y1) * 0.85


def test_draw_board_small_frame():
    try:
        import cv2  # noqa: F401
        import numpy as np
    except ImportError:
        return
    from actions.hands_tracker import draw_board
    board = HandsBoard()
    board.reset_starter()
    frame = np.zeros((180, 320, 3), np.uint8)
    draw_board(frame, board, [[(0.1, 0.1, 0.0)] * 21], {"pinch": (0.4, 0.4)})
    assert frame.shape == (180, 320, 3)
    assert int(frame.max()) > 10


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(t.__name__)
        t()
    print("ok")
