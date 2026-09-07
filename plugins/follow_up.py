"""Follow-up tracker — open loops that nag until closed."""

PLUGIN = {
    "name": "follow_up",
    "description": (
        "Track an OPEN LOOP that FRIDAY will nag about until the user closes it. "
        "Use this — NOT the reminder tool — when the user says follow up, chase, "
        "check back, if they don't reply, keep reminding me until, or open loop. "
        "A reminder fires once; a follow-up stays open and is spoken again on a "
        "spaced schedule (1 day, then 2, 4, weekly) until done / dropped / snoozed. "
        "Actions: add, list, done, drop, snooze."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "add | list | done | drop | snooze",
            },
            "who": {
                "type": "STRING",
                "description": "Person or company to follow up with (add).",
            },
            "what": {
                "type": "STRING",
                "description": "What to check — e.g. 'internship reply', 'invoice payment'.",
            },
            "due": {
                "type": "STRING",
                "description": (
                    "When to start nagging: Thursday, tomorrow, in 3 days, "
                    "next monday, or YYYY-MM-DD."
                ),
            },
            "ident": {
                "type": "STRING",
                "description": "Which loop to close/drop/snooze: name, keyword, or id.",
            },
            "days": {
                "type": "INTEGER",
                "description": "Snooze length in days (default 2).",
            },
        },
        "required": ["action"],
    },
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        from actions.followups import (
            add_followup, close_followup, drop_followup,
            list_followups, snooze_followup,
        )
        params = parameters or {}
        action = str(params.get("action") or "").strip().lower()
        if action == "add":
            result = add_followup(
                params.get("who", ""), params.get("what", ""), params.get("due", ""),
            )
        elif action in ("list", "status"):
            result = list_followups()
        elif action in ("done", "close", "complete"):
            result = close_followup(params.get("ident", ""))
        elif action in ("drop", "cancel", "forget"):
            result = drop_followup(params.get("ident", ""))
        elif action == "snooze":
            result = snooze_followup(params.get("ident", ""), params.get("days", 2))
        else:
            result = "Specify action: add, list, done, drop, or snooze."
        if player:
            try:
                player.write_log(f"SYS: follow_up — {result[:80]}")
            except Exception:
                pass
        return result
    except Exception as e:
        return f"Follow-up tracker failed: {e}"
