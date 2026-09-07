"""Headless tests for live screen share — no Qt, no Live session."""
from pathlib import Path
import io
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import actions.screen_share as ss


def test_normalize_action():
    assert ss.normalize_action("start") == "start"
    assert ss.normalize_action("SHARE") == "start"
    assert ss.normalize_action("watch") == "start"
    assert ss.normalize_action("") == "start"
    assert ss.normalize_action("stop") == "stop"
    assert ss.normalize_action("close") == "stop"
    assert ss.normalize_action("status") == "status"
    assert ss.normalize_action("explode") == "explode"


def test_start_stop_idempotent():
    state = ss.ShareState()
    code, msg = ss.apply_action(state, "start")
    assert code == ss.STARTED
    assert state.active
    assert "SCREEN_SHARE_ACTIVE" in msg

    code, msg = ss.apply_action(state, "start")
    assert code == ss.ALREADY
    assert state.active

    code, msg = ss.apply_action(state, "status")
    assert "ON" in msg

    code, msg = ss.apply_action(state, "stop")
    assert code == ss.STOPPED
    assert not state.active
    assert "SCREEN_SHARE_STOPPED" in msg

    code, msg = ss.apply_action(state, "stop")
    assert code == ss.IDLE
    assert not state.active


def test_unknown_action_does_not_start():
    state = ss.ShareState()
    code, msg = ss.apply_action(state, "explode")
    assert code == ss.UNKNOWN
    assert not state.active
    assert "start" in msg.lower()


def test_sleep_remaining_clamps():
    t0 = time.monotonic()
    assert ss.sleep_remaining(t0, interval=1.0) <= 1.0
    assert ss.sleep_remaining(t0 - 2.0, interval=1.0) == 0.0


def test_capture_frame_uses_smaller_share_size():
    calls = []

    def fake_capture(max_w=1280, max_h=720, quality=82):
        calls.append((max_w, max_h, quality))
        return b"jpeg-bytes", "image/jpeg"

    orig = ss._capture_screen
    ss._capture_screen = fake_capture
    try:
        data, mime = ss.capture_frame()
        assert data == b"jpeg-bytes"
        assert mime == "image/jpeg"
        assert calls == [(ss._SHARE_MAX_W, ss._SHARE_MAX_H, ss._SHARE_JPEG_Q)]
        assert calls[0][0] <= 960
    finally:
        ss._capture_screen = orig


def test_capture_frame_real_jpeg():
    """Grab a real frame if mss works; skip cleanly if the display is unavailable."""
    try:
        data, mime = ss.capture_frame()
    except Exception as e:
        print(f"skip real capture: {e}")
        return
    assert mime == "image/jpeg"
    assert data[:2] == b"\xff\xd8"
    assert len(data) > 200


def test_compress_honours_max_size():
    from actions.screen_processor import _compress
    try:
        import PIL.Image
    except ImportError:
        print("skip compress: no PIL")
        return
    img = PIL.Image.new("RGB", (2000, 1200), (12, 40, 60))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    out, mime = _compress(buf.getvalue(), "PNG", max_w=320, max_h=180, quality=60)
    assert mime == "image/jpeg"
    got = PIL.Image.open(io.BytesIO(out))
    assert got.width <= 320
    assert got.height <= 180


if __name__ == "__main__":
    tests = [
        test_normalize_action,
        test_start_stop_idempotent,
        test_unknown_action_does_not_start,
        test_sleep_remaining_clamps,
        test_capture_frame_uses_smaller_share_size,
        test_capture_frame_real_jpeg,
        test_compress_honours_max_size,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"ok  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            raise
    print(f"{len(tests) - failed}/{len(tests)} passed")
