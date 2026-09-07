"""Chat automation — draft/send replies on WhatsApp, Instagram, or the open chat."""

from core import confirm as confirm_gate

PLUGIN = {
    "name": "chat_automation",
    "description": (
        "Watch a chat that is already open and draft or send replies for the user. "
        "Works on WhatsApp Desktop, Instagram Direct in the user's own browser "
        "(instagram.com), or whichever chat window is in front. "
        "Actions: start, stop, status, set_persona, send_now, set_draft_only. "
        "Use when the user says chat for me, reply for me, handle this chat, "
        "chat on Instagram, chat on WhatsApp, or stop chat automation. "
        "app: auto (default — use the open window) | whatsapp | instagram. "
        "Contact is optional if the chat is already open. "
        "Default start is draft_only=true unless the user clearly says send / auto-reply. "
        "Do NOT use for phone calls (whatsapp_call / manage_calls). "
        "Do NOT start on WhatsApp group chats."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "start | stop | status | set_persona | send_now | set_draft_only",
            },
            "contact": {
                "type": "STRING",
                "description": "Person to open. Optional if that chat is already on screen.",
            },
            "app": {
                "type": "STRING",
                "description": "auto | whatsapp | instagram. Default auto = the chat in front.",
            },
            "persona": {
                "type": "STRING",
                "description": "Reply style, e.g. 'short, friendly, English'.",
            },
            "draft_only": {
                "type": "BOOLEAN",
                "description": "If true, show drafts on HUD without sending (safer). Default true.",
            },
            "message": {
                "type": "STRING",
                "description": "For send_now: optional hint; otherwise Gemini drafts from chat.",
            },
        },
        "required": ["action"],
    },
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        return _run(parameters, player)
    except Exception as e:
        return f"Chat automation failed: {e}"


def _run(parameters: dict, player=None) -> str:
    from actions.chat_watch import (
        load_prefs,
        normalize_app,
        save_prefs,
        send_now,
        start,
        status_line,
        stop,
    )
    from actions._whatsapp_ui import looks_like_group

    params = parameters or {}
    action = str(params.get("action") or "").strip().lower()
    contact = str(params.get("contact") or "").strip()
    persona = str(params.get("persona") or "").strip()
    message = str(params.get("message") or "").strip()
    app = normalize_app(str(params.get("app") or "auto"))
    draft_only = params.get("draft_only")
    if isinstance(draft_only, str):
        draft_only = draft_only.strip().lower() in ("1", "true", "yes", "on")

    if action in ("stop", "disarm", "end"):
        return stop()

    if action == "status":
        return status_line()

    if action == "set_persona":
        if not persona and not message:
            return "Give me a persona/style string to save."
        text = persona or message
        save_prefs(chat_persona=text)
        return f"Chat persona updated: {text}"

    if action == "set_draft_only":
        flag = bool(draft_only) if draft_only is not None else True
        save_prefs(chat_draft_only=flag)
        return f"Draft-only mode is now {'ON' if flag else 'OFF'}."

    if action == "send_now":
        who = contact or str(load_prefs().get("chat_contact") or "").strip()
        if who in ("open chat",):
            who = ""
        if who and looks_like_group(who) and app != "instagram":
            return "I will not auto-chat in a group. Name one person."
        return send_now(who, message=message, app=app, persona=persona)

    if action in ("start", "begin", "enable"):
        who = contact or str(load_prefs().get("chat_contact") or "").strip()
        if who in ("open chat",):
            who = ""
        if who and looks_like_group(who) and app != "instagram":
            return "I will not auto-chat in a group. Name one person."

        use_draft = True if draft_only is None else bool(draft_only)
        if persona:
            save_prefs(chat_persona=persona)

        label_app = {
            "whatsapp": "WhatsApp",
            "instagram": "Instagram",
            "auto": "the chat you have open",
        }.get(app, app)
        who_label = who or "the open chat"

        def _do() -> str:
            return start(
                who,
                app=app,
                persona=persona,
                draft_only=use_draft,
                player=player,
            )

        mode = "draft-only (safer)" if use_draft else "AUTO-SEND"
        return confirm_gate.request(
            key=f"chat:{app}:{who_label}",
            title="Chat automation",
            detail=(
                f"Let FRIDAY chat on {label_app} with {who_label} ({mode})?\n"
                "You can say 'stop chat automation' anytime."
            ),
            run=_do,
        )

    return "Unknown action. Use start, stop, status, set_persona, set_draft_only, or send_now."
