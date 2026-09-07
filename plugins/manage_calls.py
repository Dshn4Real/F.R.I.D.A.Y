"""Manage WhatsApp Calls — arm watcher, accept/decline, auto-reply."""

PLUGIN = {
    "name": "manage_calls",
    "description": (
        "Manage incoming WhatsApp calls hands-free. "
        "Actions: arm (start watching), disarm (stop), accept, decline, "
        "auto_reply (decline + send custom text), set_auto_reply, status, "
        "set_default (banner|decline|accept). "
        "Use when the user mentions incoming calls, accept/decline a call, "
        "or wants Friday to handle WhatsApp ringing. "
        "Do NOT use whatsapp_call for incoming calls — that is outbound only. "
        "Default after arm is banner (announce only). Never set_default to accept "
        "unless the user clearly asked to auto-answer."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "arm | disarm | accept | decline | auto_reply | "
                    "set_auto_reply | status | set_default"
                ),
            },
            "message": {
                "type": "STRING",
                "description": "Custom auto-reply text for decline/auto_reply/set_auto_reply.",
            },
            "contact": {
                "type": "STRING",
                "description": "Optional contact to open when sending auto-reply after decline.",
            },
            "default_action": {
                "type": "STRING",
                "description": "For set_default: banner | decline | accept",
            },
        },
        "required": ["action"],
    },
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        return _run(parameters, player)
    except Exception as e:
        return f"Call manager failed: {e}"


def _run(parameters: dict, player=None) -> str:
    from actions._whatsapp_ui import (
        accept_call,
        decline_call,
        decline_with_message,
        get_incoming_call_state,
        load_prefs,
        save_prefs,
        set_incoming_callback,
        start_call_watcher,
        stop_call_watcher,
    )

    params = parameters or {}
    action = str(params.get("action") or "").strip().lower()
    message = str(params.get("message") or "").strip()
    contact = str(params.get("contact") or "").strip()
    default_action = str(params.get("default_action") or "").strip().lower()

    if action in ("arm", "start", "watch", "enable"):
        def _on_incoming(st: dict) -> None:
            if not player:
                return
            try:
                from plugins.whatsapp_call import caller_from_title

                kind = st.get("kind") or "voice"
                who = caller_from_title(str(st.get("title") or ""))
                player.write_log(
                    f"SYS: Incoming WhatsApp {kind} call from {who} — "
                    "use the HUD card, or say accept / decline / auto_reply."
                )
                if hasattr(player, "show_incoming_call"):
                    player.show_incoming_call(who, kind)
                elif hasattr(player, "show_content"):
                    player.show_content(
                        "INCOMING WHATSAPP CALL",
                        f"{who} · {kind} call\nACCEPT · DECLINE · MSG on the HUD",
                    )
            except Exception:
                pass

        set_incoming_callback(_on_incoming)
        msg = start_call_watcher()
        if player:
            try:
                player.write_log("SYS: WhatsApp call watcher armed.")
            except Exception:
                pass
        return msg

    if action in ("disarm", "stop", "disable"):
        if player and hasattr(player, "hide_incoming_call"):
            try:
                player.hide_incoming_call()
            except Exception:
                pass
        msg = stop_call_watcher()
        if player:
            try:
                player.write_log("SYS: WhatsApp call watcher disarmed.")
            except Exception:
                pass
        return msg

    if action == "accept":
        if player and hasattr(player, "hide_incoming_call"):
            try:
                player.hide_incoming_call()
            except Exception:
                pass
        return accept_call()

    if action == "decline":
        if player and hasattr(player, "hide_incoming_call"):
            try:
                player.hide_incoming_call()
            except Exception:
                pass
        return decline_call()

    if action in ("auto_reply", "decline_reply"):
        if player and hasattr(player, "hide_incoming_call"):
            try:
                player.hide_incoming_call()
            except Exception:
                pass
        prefs = load_prefs()
        text = message or str(prefs.get("auto_reply_text") or "")
        if message:
            save_prefs(auto_reply_text=message)
        return decline_with_message(contact, text)

    if action == "set_auto_reply":
        if not message:
            return "Tell me the auto-reply text to save."
        save_prefs(auto_reply_text=message)
        return f"Saved WhatsApp auto-reply: {message}"

    if action == "set_default":
        val = default_action or message
        if val not in ("banner", "decline", "accept"):
            return "default_action must be banner, decline, or accept."
        save_prefs(default_call_action=val)
        return f"When a call rings, default action is now: {val}."

    if action == "status":
        prefs = load_prefs()
        st = get_incoming_call_state()
        ringing = "yes" if st.get("incoming") else "no"
        return (
            f"Call watcher armed={bool(prefs.get('calls_armed'))}, "
            f"default={prefs.get('default_call_action')}, "
            f"ringing_now={ringing}, "
            f"auto_reply=\"{prefs.get('auto_reply_text')}\"."
        )

    return (
        "Unknown action. Use arm, disarm, accept, decline, auto_reply, "
        "set_auto_reply, set_default, or status."
    )
