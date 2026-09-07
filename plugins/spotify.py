"""
Spotify control for FRIDAY.

TWO PATHS
    Media keys (always work, free Spotify): play/pause, next, previous,
    stop, volume up/down. These go through the OS, so they hit whatever
    player is in the foreground — usually Spotify if it is open.

    Web API (needs a free Spotify developer app + later Premium for
    playback): connect, now_playing, search, play a track/playlist.
    Without Premium, search still works and playback API returns a
    clear "needs Premium" sentence instead of pretending it played.

OAuth is PKCE (no client secret). Tokens live encrypted in data/spotify.json.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import secrets
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

from core.secure_store import read_json, write_json

PLUGIN = {
    "name": "spotify",
    "description": (
        "Control Spotify / media playback. "
        "Use for play, pause, next, previous, what's playing, "
        "play a song/artist/playlist, or connect Spotify. "
        "Do NOT use this for system volume — that is computer_settings. "
        "Do NOT use youtube_video or open_app for Spotify playback. "
        "Play/pause/skip work immediately via media keys (no Premium). "
        "Named-track / playlist playback needs a one-time Spotify connect "
        "and later Premium."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "play | pause | next | previous | stop | "
                    "now_playing | search | play_track | play_playlist | connect | status | disconnect"
                ),
            },
            "query": {
                "type": "STRING",
                "description": "Song, artist, or playlist name for search / play_track / play_playlist.",
            },
            "client_id": {
                "type": "STRING",
                "description": "Spotify app Client ID — only needed the first time you connect.",
            },
        },
        "required": ["action"],
    },
}

_REDIRECT     = "http://127.0.0.1:8899/callback"
_SCOPES       = "user-read-playback-state user-modify-playback-state user-read-currently-playing"
_TOKEN_URL    = "https://accounts.spotify.com/api/token"
_AUTH_URL     = "https://accounts.spotify.com/authorize"
_API          = "https://api.spotify.com/v1"


def _base_dir() -> Path:
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _store_path() -> Path:
    return _base_dir() / "data" / "spotify.json"


def _config_path() -> Path:
    return _base_dir() / "config" / "api_keys.json"


def _load_store() -> dict:
    try:
        data = read_json(_store_path())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_store(data: dict) -> None:
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    write_json(p, data)


def _client_id(override: str = "") -> str:
    if override.strip():
        return override.strip()
    store = _load_store()
    if store.get("client_id"):
        return store["client_id"]
    try:
        cfg = read_json(_config_path())
        if isinstance(cfg, dict):
            return (cfg.get("spotify_client_id") or "").strip()
    except Exception:
        pass
    return ""


# ── Media keys (work without Premium / without OAuth) ─────────────────────────

_VK = {
    "next":       0xB0,
    "previous":   0xB1,
    "stop":       0xB2,
    "play":       0xB3,
    "pause":      0xB3,
}

# Injected in tests so we never press real keys.
_press_media = None


def _media(action: str) -> str:
    vk = _VK.get(action)
    if vk is None:
        return f"Unknown media action: {action}"
    press = _press_media
    if press is not None:
        press(vk)
    else:
        import ctypes
        KEYUP = 0x0002
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
        time.sleep(0.03)
        ctypes.windll.user32.keybd_event(vk, 0, KEYUP, 0)
    labels = {
        "play": "Play/pause toggled.",
        "pause": "Play/pause toggled.",
        "next": "Skipped to next track.",
        "previous": "Went to previous track.",
        "stop": "Stop sent.",
    }
    return labels.get(action, f"Sent {action}.")


# ── Spotify Web API ───────────────────────────────────────────────────────────

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _refresh_access(store: dict) -> dict:
    import requests
    refresh = store.get("refresh_token")
    cid = store.get("client_id") or _client_id()
    if not refresh or not cid:
        raise RuntimeError("not connected")
    resp = requests.post(_TOKEN_URL, data={
        "grant_type":    "refresh_token",
        "refresh_token": refresh,
        "client_id":     cid,
    }, timeout=15)
    if resp.status_code != 200:
        raise RuntimeError(f"token refresh failed ({resp.status_code})")
    body = resp.json()
    store["access_token"] = body["access_token"]
    store["expires_at"] = time.time() + int(body.get("expires_in", 3600)) - 30
    if body.get("refresh_token"):
        store["refresh_token"] = body["refresh_token"]
    _save_store(store)
    return store


def _access_token() -> str:
    store = _load_store()
    if not store.get("access_token"):
        raise RuntimeError("not connected")
    if time.time() >= float(store.get("expires_at") or 0):
        store = _refresh_access(store)
    return store["access_token"]


def _api(method: str, path: str, **kw):
    import requests
    token = _access_token()
    url = path if path.startswith("http") else f"{_API}{path}"
    resp = requests.request(
        method, url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
        **kw,
    )
    if resp.status_code == 401:
        store = _refresh_access(_load_store())
        resp = requests.request(
            method, url,
            headers={"Authorization": f"Bearer {store['access_token']}"},
            timeout=15,
            **kw,
        )
    return resp


def _premium_hint(resp) -> str | None:
    if resp.status_code == 403:
        try:
            reason = (resp.json().get("error") or {}).get("reason", "")
        except Exception:
            reason = ""
        if reason == "PREMIUM_REQUIRED" or "premium" in resp.text.lower():
            return ("That playback command needs Spotify Premium. "
                    "Play/pause/skip still work without it — I just sent the media key.")
    if resp.status_code == 404:
        return ("No active Spotify device. Open Spotify on this PC, play any "
                "track once, then ask again.")
    return None


def _connect(client_id: str) -> str:
    cid = _client_id(client_id)
    if not cid:
        return (
            "To connect Spotify once: create a free app at "
            "https://developer.spotify.com/dashboard — set Redirect URI to "
            f"{_REDIRECT} — then say 'connect Spotify' and give me the Client ID, "
            "or put spotify_client_id in config/api_keys.json."
        )

    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    state = secrets.token_urlsafe(16)
    qs = urllib.parse.urlencode({
        "client_id":             cid,
        "response_type":         "code",
        "redirect_uri":          _REDIRECT,
        "scope":                 _SCOPES,
        "code_challenge_method": "S256",
        "code_challenge":        challenge,
        "state":                 state,
    })
    auth = f"{_AUTH_URL}?{qs}"

    result: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/callback":
                self.send_error(404)
                return
            q = urllib.parse.parse_qs(parsed.query)
            result["code"] = (q.get("code") or [""])[0]
            result["state"] = (q.get("state") or [""])[0]
            result["error"] = (q.get("error") or [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body style='font-family:sans-serif;background:#111;color:#eee'>"
                b"<p>FRIDAY is connected. You can close this tab.</p></body></html>"
            )

        def log_message(self, *_):
            return

    try:
        server = http.server.HTTPServer(("127.0.0.1", 8899), Handler)
    except OSError:
        return "Port 8899 is busy — close whatever is using it and try connect again."

    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    webbrowser.open(auth)
    thread.join(timeout=120)
    try:
        server.server_close()
    except Exception:
        pass

    if result.get("error"):
        return f"Spotify login was cancelled ({result['error']})."
    if not result.get("code") or result.get("state") != state:
        return "Spotify login did not finish — try connect again."

    import requests
    resp = requests.post(_TOKEN_URL, data={
        "grant_type":    "authorization_code",
        "code":          result["code"],
        "redirect_uri":  _REDIRECT,
        "client_id":     cid,
        "code_verifier": verifier,
    }, timeout=15)
    if resp.status_code != 200:
        return f"Spotify token exchange failed ({resp.status_code})."
    body = resp.json()
    _save_store({
        "client_id":     cid,
        "access_token":  body["access_token"],
        "refresh_token": body.get("refresh_token", ""),
        "expires_at":    time.time() + int(body.get("expires_in", 3600)) - 30,
    })
    return "Spotify connected. Named-track playback still needs Premium; play/pause/skip work now."


def _now_playing() -> str:
    try:
        resp = _api("GET", "/me/player/currently-playing")
    except RuntimeError as e:
        if "not connected" in str(e):
            return "Spotify is not connected yet. Say connect Spotify first — play/pause still work without it."
        return str(e)
    if resp.status_code == 204 or not resp.content:
        return "Nothing is playing on Spotify."
    if resp.status_code != 200:
        return f"Could not read now-playing ({resp.status_code})."
    data = resp.json()
    item = data.get("item") or {}
    name = item.get("name") or "Unknown track"
    artists = ", ".join(a.get("name", "") for a in item.get("artists") or [])
    playing = "Playing" if data.get("is_playing") else "Paused"
    return f"{playing}: {name}" + (f" by {artists}" if artists else "")


def _search_play(query: str, kind: str) -> str:
    query = (query or "").strip()
    if not query:
        return "Say what to play — a song, artist, or playlist name."
    try:
        qtype = "playlist" if kind == "playlist" else "track"
        resp = _api("GET", f"/search?{urllib.parse.urlencode({'q': query, 'type': qtype, 'limit': 1})}")
    except RuntimeError as e:
        if "not connected" in str(e):
            return ("Named-track playback needs a one-time Spotify connect. "
                    "Until then I can play/pause/skip with media keys.")
        return str(e)
    if resp.status_code != 200:
        return f"Spotify search failed ({resp.status_code})."
    items = ((resp.json().get(qtype + "s") or {}).get("items")) or []
    if not items:
        return f"No {qtype} found for '{query}'."
    uri = items[0].get("uri")
    title = items[0].get("name", query)
    body = {"context_uri": uri} if kind == "playlist" else {"uris": [uri]}
    play = _api("PUT", "/me/player/play", json=body)
    hint = _premium_hint(play)
    if hint:
        return hint
    if play.status_code in (200, 202, 204):
        return f"Playing {title}."
    return f"Could not start playback ({play.status_code})."


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        params = parameters or {}
        action = str(params.get("action") or "").strip().lower()
        query = str(params.get("query") or "").strip()

        if action in ("volume_up", "volume_down"):
            from actions.computer_settings import computer_settings
            result = computer_settings({"action": action}, player=player)
        elif action in _VK:
            result = _media(action)
        elif action == "connect":
            result = _connect(str(params.get("client_id") or ""))
        elif action == "disconnect":
            p = _store_path()
            if p.exists():
                p.unlink()
            result = "Spotify disconnected. Media keys still work."
        elif action == "status":
            store = _load_store()
            if store.get("access_token"):
                result = "Spotify API connected."
            elif _client_id():
                result = "Client ID saved, but not logged in yet — say connect Spotify."
            else:
                result = "Not connected. Play/pause/skip still work via media keys."
        elif action == "now_playing":
            result = _now_playing()
        elif action == "search":
            result = _search_play(query, "track") if query else _now_playing()
        elif action in ("play_track", "play_song"):
            result = _search_play(query, "track")
        elif action == "play_playlist":
            result = _search_play(query, "playlist")
        else:
            result = (
                "Unknown Spotify action. Use play, pause, next, previous, "
                "now_playing, play_track, play_playlist, connect, or status."
            )
        if player:
            try:
                player.write_log(f"SYS: spotify — {result[:80]}")
            except Exception:
                pass
        return result
    except Exception as e:
        return f"Spotify plugin failed: {e}"
