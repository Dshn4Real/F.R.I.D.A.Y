"""Host the fullstack-agent barehands board locally and talk to it.

Friday starts server.py on localhost:8794, writes the ring state, and
POSTs cards onto the glass. The UI embeds that page in a Qt WebEngine
view — same board as fullstack-agent, owned by Friday.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

PORT = 8794
STAGE_URL = f"http://127.0.0.1:{PORT}/stage.html"

_PROC: subprocess.Popen | None = None
_LOCK = threading.Lock()

_RING = {
    "listening": "listening",
    "speaking": "speaking",
    "thinking": "thinking",
    "idle": "idle",
    "sleeping": "idle",
    "muted": "idle",
    "interrupted": "listening",
    "follow_up": "listening",
}


def _root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def board_dir() -> Path:
    home = Path.home()
    candidates = [
        Path(r"c:\Users\disha\Friday\barehands"),
        _root() / "integrations" / "barehands",
        home / "Friday" / "barehands",
        home / "my-agent" / "barehands",
    ]
    for path in candidates:
        if (path / "server.py").is_file() and (path / "stage.html").is_file():
            return path
    return _root() / "integrations" / "barehands"


def installed() -> bool:
    d = board_dir()
    return (d / "server.py").is_file() and (d / "stage.html").is_file()


def is_up() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.4):
            return True
    except OSError:
        return False


def ring_word(hud_state: str) -> str:
    key = (hud_state or "").strip().lower()
    return _RING.get(key, "idle")


def write_ring(hud_state: str) -> None:
    if not installed():
        return
    path = board_dir() / "state" / "state"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(ring_word(hud_state), encoding="utf-8")
    except Exception:
        pass


def ensure_config(name: str = "FRIDAY") -> None:
    d = board_dir()
    if not d.is_dir():
        return
    cfg_path = d / "barehands.json"
    payload = {
        "name": name or "FRIDAY",
        "port": PORT,
        "orbs": [
            {"title": "Notes", "path": "sample-notes", "kind": "notes"},
            {"title": "Props", "path": "media", "kind": "media"},
        ],
    }
    if cfg_path.exists():
        try:
            existing = json.loads(cfg_path.read_text(encoding="utf-8"))
            existing["name"] = payload["name"]
            existing["port"] = PORT
            if not existing.get("orbs"):
                existing["orbs"] = payload["orbs"]
            payload = existing
        except Exception:
            pass
    cfg_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (d / "state").mkdir(exist_ok=True)


def start_server(name: str = "FRIDAY") -> str | None:
    """Start the local board if needed. Returns an error sentence, or None."""
    global _PROC
    if not installed():
        return (
            "The fullstack hands board is missing. It should live at "
            "c:\\Users\\disha\\Friday\\barehands with server.py and stage.html."
        )
    with _LOCK:
        ensure_config(name)
        if is_up():
            return None
        d = board_dir()
        try:
            _PROC = subprocess.Popen(
                [sys.executable, str(d / "server.py")],
                cwd=str(d),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            return f"Couldn't start the hands board server. ({e})"

        for _ in range(50):
            if is_up():
                write_ring("listening")
                return None
            if _PROC.poll() is not None:
                _PROC = None
                return "The hands board server exited immediately."
            time.sleep(0.1)
        return "The hands board server started but did not answer in time."


def stop_server() -> None:
    global _PROC
    write_ring("idle")
    if _PROC is None:
        return
    try:
        _PROC.terminate()
        try:
            _PROC.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _PROC.kill()
    except Exception:
        pass
    _PROC = None


_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".webm"}


def media_misc() -> Path:
    path = board_dir() / "media" / "misc"
    path.mkdir(parents=True, exist_ok=True)
    return path


def stage_image(source: str) -> tuple[str | None, str]:
    """Copy any local image into media/misc and return (src, error).

    src is the airlock-relative path the board expects, e.g. misc/photo.png.
    """
    raw = (source or "").strip().strip('"')
    if not raw:
        return None, "Tell me the image path."
    src = Path(raw).expanduser()
    if not src.is_file():
        return None, f"I couldn't find that file: {src}"
    ext = src.suffix.lower()
    if ext not in _IMAGE_EXTS:
        return None, "Use a png, jpg, webp, gif, or webm image."
    dest_dir = media_misc()
    try:
        dest_dir = dest_dir.resolve()
        src = src.resolve()
    except Exception as e:
        return None, str(e)
    media_root = (board_dir() / "media").resolve()
    if media_root in src.parents or src.parent == media_root:
        return src.relative_to(media_root).as_posix(), ""
    safe = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in src.name).strip() or f"image{ext}"
    dest = dest_dir / safe
    n = 1
    while dest.exists() and dest.resolve() != src:
        dest = dest_dir / f"{Path(safe).stem}_{n}{ext}"
        n += 1
    try:
        import shutil
        shutil.copy2(src, dest)
    except Exception as e:
        return None, f"Couldn't copy the image into the board folder. ({e})"
    return f"misc/{dest.name}", ""


def post_cmd(cmd: dict) -> tuple[bool, str]:
    data = json.dumps(cmd).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/cmd",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            if 200 <= getattr(resp, "status", 200) < 300:
                return True, ""
            return False, f"board returned {resp.status}"
    except urllib.error.HTTPError as e:
        return False, f"board rejected that ({e.code})"
    except Exception as e:
        return False, str(e)
