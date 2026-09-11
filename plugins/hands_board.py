"""
Hands board — local MediaPipe 21-point skeleton in the FRIDAY HUD.

Same camera-control loop as fullstack-agent / barehands: pinch-drag, tap,
two-hand scale, hold-to-rotate, flick, clap reset, claw force-pull,
present / yank / hover. Images from a local file path.
Do NOT use screen_process for this — that only looks at the camera.
"""

from __future__ import annotations

PLUGIN = {
    "name": "hands_board",
    "description": (
        "Open FRIDAY's local hand-tracking glass board inside the HUD. "
        "Uses MediaPipe 21-point skeletal landmarks — the fullstack-agent "
        "barehands gestures, running in-process (not a browser). "
        "Use when they say: open hands board, hand tracking, move things with my "
        "hands, gesture board, pinch cards, air board, claw, clap the board, "
        "put a 3D model on the glass, hologram, explode it, assemble it, "
        "put this photo on the glass, drop the file on the board. "
        "Actions: open, close, present (spotlight), add_card, add_img, add_model "
        "(glb/gltf/obj or a demo engine if no path), hand, yank, hover, "
        "explode, assemble, reset, clear, status. "
        "For images/models, image can be a full path OR just a filename — "
        "FRIDAY searches Desktop/Downloads/Pictures. Leave image empty to "
        "use the current HUD file or open a picker. Files go on the glass "
        "in place; do not upload them first. "
        "Do NOT use screen_process or close_camera for this."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "open | close | present | add_card | add_img | add_model | hand | "
                    "yank | hover | explode | assemble | reset | clear | status"
                ),
            },
            "title": {
                "type": "STRING",
                "description": "Title for present / add_card / add_img / add_model / yank / hover / explode.",
            },
            "body": {
                "type": "STRING",
                "description": "Card body text for present / add_card.",
            },
            "image": {
                "type": "STRING",
                "description": (
                    "Full local path OR just a filename (Desktop/Downloads/Pictures). "
                    "Leave empty to use the current HUD file or pick one. "
                    "Goes on the glass in place — do not upload into FRIDAY first."
                ),
            },
            "x": {
                "type": "NUMBER",
                "description": "Optional 0-1 horizontal position for add_card.",
            },
            "y": {
                "type": "NUMBER",
                "description": "Optional 0-1 vertical position for add_card.",
            },
        },
        "required": ["action"],
    },
}


def _log(player, text: str) -> None:
    if not player:
        return
    try:
        player.write_log(text)
    except Exception:
        pass


def _need_ui(player) -> str | None:
    if player is None or not hasattr(player, "start_hands_board"):
        return "Hands board needs the FRIDAY window running."
    return None


def _ensure_open(player) -> None:
    if not getattr(player, "hands_board_open", lambda: False)():
        player.stop_camera_stream()
        player.start_hands_board()


def _is_model(path: str) -> bool:
    raw = (path or "").strip()
    if raw.startswith("demo:"):
        return True
    try:
        from actions.hands_models import is_model_path
        return is_model_path(raw)
    except Exception:
        return False


def _player_file(player) -> str:
    if player is None:
        return ""
    getter = getattr(player, "current_file", None)
    try:
        if callable(getter):
            return str(getter() or "")
    except Exception:
        pass
    return str(getattr(player, "_current_file", "") or "")


def _board_file(hint: str, player) -> str:
    current = _player_file(player)
    try:
        from actions.hands_tracker import resolve_media_path
        return resolve_media_path(hint, current)
    except Exception:
        return (hint or current or "").strip()


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        params = parameters or {}
        action = str(params.get("action") or "").strip().lower()
        title = str(params.get("title") or "")
        body = str(params.get("body") or "")
        image = str(params.get("image") or params.get("src") or "").strip()

        if action in ("status", "state"):
            open_ = bool(getattr(player, "hands_board_open", lambda: False)())
            result = (
                f"Hands board is {'open' if open_ else 'closed'} "
                "(local 21-point skeleton — pinch, tap, scale, clap, claw)."
            )
        elif action in ("open", "start", "launch"):
            miss = _need_ui(player)
            if miss:
                return miss
            player.stop_camera_stream()
            player.start_hands_board()
            result = (
                "Hands board is open. Pinch thumb and index to grab, tap to open, "
                "two hands to scale, hold still to rotate, peace to explode a 3D model, "
                "thumbs up to rebuild, clap to reset, claw to force-pull. "
                "C cycles cameras, D debug, R reset."
            )
        elif action in ("close", "stop", "quit"):
            miss = _need_ui(player)
            if miss:
                return miss
            player.stop_hands_board()
            result = "Hands board closed. The HUD camera is free again."
        elif action in ("present", "show_me", "spotlight"):
            miss = _need_ui(player)
            if miss:
                return miss
            _ensure_open(player)
            if image:
                image = _board_file(image, player)
            if image and _is_model(image) and hasattr(player, "present_hands_model"):
                player.present_hands_model(image, title)
                result = "Put a 3D model center stage. Hold still to spin it."
            elif image:
                player.present_hands_image(image, title)
                result = "Put that image center stage. Pinch to take it."
            else:
                player.present_hands_card(title, body)
                result = f"Spotlit '{(title or 'FRIDAY').strip()}' on the board."
        elif action in ("add_card", "add", "card"):
            miss = _need_ui(player)
            if miss:
                return miss
            _ensure_open(player)
            if image:
                image = _board_file(image, player)
            if image and _is_model(image) and hasattr(player, "add_hands_model"):
                player.add_hands_model(image, title)
                result = "Dropped a 3D model onto the board."
            elif image:
                player.add_hands_image(image, title)
                result = "Dropped that image onto the board."
            else:
                x = params.get("x")
                y = params.get("y")
                if hasattr(player, "add_hands_card_xy"):
                    player.add_hands_card_xy(title, body, x, y)
                else:
                    player.add_hands_card(title, body)
                result = f"Dropped '{(title or 'Note').strip()}' onto the board."
        elif action in ("add_img", "add_image", "image", "photo"):
            miss = _need_ui(player)
            if miss:
                return miss
            image = _board_file(image, player)
            if not image:
                _ensure_open(player)
                if hasattr(player, "pick_hands_file"):
                    player.pick_hands_file()
                    result = "Pick a file — it goes on the glass, no upload."
                else:
                    return "Drop the file on the glass, or tell me the filename on your Desktop or Downloads."
            else:
                _ensure_open(player)
                if _is_model(image) and hasattr(player, "add_hands_model"):
                    player.add_hands_model(image, title)
                    result = "Put that 3D model on the glass. Peace explodes it, thumbs up brings it back."
                else:
                    player.add_hands_image(image, title)
                    result = "Put that file on the glass. Pinch to move it."
        elif action in ("add_model", "model", "3d", "hologram", "holo"):
            miss = _need_ui(player)
            if miss:
                return miss
            _ensure_open(player)
            if image:
                image = _board_file(image, player)
            if hasattr(player, "add_hands_model"):
                player.add_hands_model(image, title)
            else:
                player.add_hands_image(image or "demo://engine", title)
            result = (
                "Dropped a 3D hologram on the glass. Hold still to spin, "
                "two hands to scale, peace sign to explode, thumbs up to rebuild."
            )
        elif action in ("hand", "give"):
            miss = _need_ui(player)
            if miss:
                return miss
            _ensure_open(player)
            if image:
                image = _board_file(image, player)
            if hasattr(player, "hand_hands_item"):
                player.hand_hands_item(title, body, image)
            elif image:
                player.add_hands_image(image, title)
            else:
                player.add_hands_card(title, body)
            result = "Handed it to you on the glass."
        elif action in ("yank",):
            miss = _need_ui(player)
            if miss:
                return miss
            if not getattr(player, "hands_board_open", lambda: False)():
                return "The hands board is not open."
            player.yank_hands_item(title)
            result = f"Yanked '{title or 'it'}' off the board."
        elif action in ("hover",):
            miss = _need_ui(player)
            if miss:
                return miss
            if not getattr(player, "hands_board_open", lambda: False)():
                return "The hands board is not open."
            player.hover_hands_item(title)
            result = f"Pulsed '{title or 'it'}' for attention."
        elif action in ("reset", "respawn"):
            miss = _need_ui(player)
            if miss:
                return miss
            _ensure_open(player)
            player.reset_hands_board()
            result = "Board reset. The ring is center stage."
        elif action in ("clear",):
            miss = _need_ui(player)
            if miss:
                return miss
            if not getattr(player, "hands_board_open", lambda: False)():
                result = "The hands board is not open."
            else:
                player.clear_hands_board()
                result = "Swept the board clean."
        elif action in ("clap",):
            miss = _need_ui(player)
            if miss:
                return miss
            _ensure_open(player)
            player.reset_hands_board()
            result = "Clap — ring center stage."
        elif action in ("explode",):
            miss = _need_ui(player)
            if miss:
                return miss
            _ensure_open(player)
            if hasattr(player, "explode_hands_model"):
                player.explode_hands_model(title)
            result = "Exploding the 3D model. Hold a peace sign on the glass to do this by hand."
        elif action in ("assemble", "rebuild"):
            miss = _need_ui(player)
            if miss:
                return miss
            _ensure_open(player)
            if hasattr(player, "assemble_hands_model"):
                player.assemble_hands_model(title)
            result = "Assembling the 3D model. Hold a thumbs up on the glass to do this by hand."
        else:
            result = (
                "Specify action: open, close, present, add_card, add_img, add_model, "
                "hand, yank, hover, explode, assemble, reset, clear, or status."
            )

        _log(player, f"SYS: hands_board — {result[:90]}")
        return result
    except Exception as e:
        return f"Hands board failed: {e}"
