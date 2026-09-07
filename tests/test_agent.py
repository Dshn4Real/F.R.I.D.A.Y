"""
tests/test_agent.py — headless exercise of the core/agent.py loop.

No Qt, no audio, no network: the planner LLM is replaced by a scripted stub and
tools are recording fakes. Run directly:

    python tests/test_agent.py
"""

import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.agent as agent_mod
from core.agent import AgentRunner, _parse_json


# ── Harness ───────────────────────────────────────────────────────────────────

class Harness:
    """Collects every callback the runner makes, and answers the plan panel."""

    def __init__(self, tools, llm_responses, confirm_pending=lambda: ""):
        self.says:  list[str] = []
        self.logs:  list[str] = []
        self.shown: list = []
        self.steps: list = []
        self.finished: list = []
        self.hidden = 0
        self._plan_shown = threading.Event()
        self._done = threading.Event()
        self._llm_responses = list(llm_responses)

        decls = [
            {"name": n, "description": f"fake {n}",
             "parameters": {"type": "OBJECT", "properties": {}}}
            for n in tools
        ]
        self.runner = AgentRunner(
            tools=tools,
            tool_declarations=decls,
            get_api_key=lambda: "fake",
            say=self._say,
            log=self.logs.append,
            ui_show_plan=self._show,
            ui_update_step=lambda i, s, n: self.steps.append((i, s, n)),
            ui_finish_plan=self._finish,
            ui_hide_plan=self._hide,
            confirm_pending=confirm_pending,
        )
        # Replace the network call with the script.
        self.runner._llm = self._fake_llm

    def _fake_llm(self, prompt: str) -> str:
        if not self._llm_responses:
            raise AssertionError("LLM called more times than scripted")
        return self._llm_responses.pop(0)

    def _say(self, msg):
        self.says.append(msg)
        if "[TASK_DONE]" in msg or "[TASK_FAILED]" in msg or "[TASK_CANCELLED]" in msg:
            self._done.set()

    def _show(self, goal, steps):
        self.shown.append((goal, steps))
        self._plan_shown.set()

    def _finish(self, summary, ok):
        self.finished.append((summary, ok))

    def _hide(self):
        self.hidden += 1
        self._done.set()

    def wait_plan(self, timeout=5):
        assert self._plan_shown.wait(timeout), "plan never reached the UI"

    def wait_done(self, timeout=10):
        assert self._done.wait(timeout), "task never finished"
        time.sleep(0.05)   # let _clear() run


def plan_json(*steps):
    return json.dumps({"steps": list(steps), "infeasible": ""})


PASS = 0

def check(name, cond, detail=""):
    global PASS
    assert cond, f"FAIL: {name} {detail}"
    PASS += 1
    print(f"  ok - {name}")


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_parse_json():
    check("bare json", _parse_json('{"a": 1}') == {"a": 1})
    check("fenced json", _parse_json('```json\n{"a": 1}\n```') == {"a": 1})
    check("prose-wrapped", _parse_json('Here you go: {"a": {"b": 2}} done') == {"a": {"b": 2}})
    check("garbage gives None", _parse_json("no json here") is None)


def test_happy_path():
    calls = []
    tools = {
        "open_app":   lambda a: calls.append(("open_app", a)) or "Opened.",
        "web_search": lambda a: calls.append(("web_search", a)) or "Found 3 results.",
    }
    h = Harness(tools, [plan_json(
        {"tool": "open_app", "args": {"app_name": "Chrome"}, "title": "Open Chrome", "destructive": False},
        {"tool": "web_search", "args": {"query": "cats"}, "title": "Search for cats", "destructive": False},
    )])
    msg = h.runner.start("open chrome and search cats")
    check("start returns planning ack", "[TASK_PLANNING]" in msg)
    h.wait_plan()
    check("plan shown with 2 steps", len(h.shown[0][1]) == 2)
    check("plan-ready say", any("[TASK_PLAN_READY]" in s for s in h.says))
    h.runner.on_plan_answer(True)
    h.wait_done()
    check("both tools ran in order", [c[0] for c in calls] == ["open_app", "web_search"])
    check("args forwarded", calls[0][1] == {"app_name": "Chrome"})
    check("finish ok=True", h.finished and h.finished[0][1] is True)
    check("done say", any("[TASK_DONE]" in s for s in h.says))
    check("runner freed", not h.runner.busy)


def test_decline():
    calls = []
    tools = {"open_app": lambda a: calls.append(a) or "Opened."}
    h = Harness(tools, [plan_json(
        {"tool": "open_app", "args": {}, "title": "Open something", "destructive": False},
    )])
    h.runner.start("open something")
    h.wait_plan()
    h.runner.on_plan_answer(False)
    h.wait_done()
    check("nothing ran on decline", calls == [])
    check("cancelled say", any("[TASK_CANCELLED]" in s for s in h.says))
    check("panel hidden", h.hidden >= 1)
    check("runner freed after decline", not h.runner.busy)


def test_retry_with_fixed_args():
    attempts = []
    def flaky(a):
        attempts.append(dict(a))
        if a.get("path") != "C:/right":
            return "Error: file not found"
        return "Moved 4 files."
    h = Harness({"file_controller": flaky}, [
        plan_json({"tool": "file_controller", "args": {"path": "C:/wrong"},
                   "title": "Move the files", "destructive": False}),
        json.dumps({"decision": "retry", "fixed_args": {"path": "C:/right"},
                    "reason": "wrong path"}),
    ])
    h.runner.start("move files")
    h.wait_plan()
    h.runner.on_plan_answer(True)
    h.wait_done()
    check("retried once", len(attempts) == 2)
    check("retry used fixed args", attempts[1] == {"path": "C:/right"})
    check("retry finish ok", h.finished[0][1] is True)


def test_abort_skips_rest():
    ran = []
    tools = {
        "open_app":   lambda a: ran.append("a") or "Error: app not found",
        "web_search": lambda a: ran.append("b") or "ok",
    }
    h = Harness(tools, [
        plan_json(
            {"tool": "open_app", "args": {}, "title": "Open the app", "destructive": False},
            {"tool": "web_search", "args": {}, "title": "Search inside it", "destructive": False},
        ),
        json.dumps({"decision": "abort", "fixed_args": {}, "reason": "app missing"}),
    ])
    h.runner.start("do both")
    h.wait_plan()
    h.runner.on_plan_answer(True)
    h.wait_done()
    check("second step never ran", ran == ["a"])
    check("abort finish ok=False", h.finished[0][1] is False)
    check("later step marked skipped", any(s[1] == "skipped" for s in h.steps))


def test_tool_exception_continue():
    def boom(a):
        raise RuntimeError("kaput")
    h = Harness({"open_app": boom}, [
        plan_json({"tool": "open_app", "args": {}, "title": "Open the app", "destructive": False}),
        json.dumps({"decision": "continue", "fixed_args": {}, "reason": "fine"}),
    ])
    h.runner.start("open")
    h.wait_plan()
    h.runner.on_plan_answer(True)
    h.wait_done()
    check("exception captured not raised", h.finished)


def test_stop_mid_run():
    started = threading.Event()
    release = threading.Event()
    ran = []
    def slow(a):
        started.set()
        release.wait(5)
        return "slow done"
    tools = {"open_app": slow, "web_search": lambda a: ran.append("x") or "ok"}
    h = Harness(tools, [plan_json(
        {"tool": "open_app", "args": {}, "title": "Slow step", "destructive": False},
        {"tool": "web_search", "args": {}, "title": "Never runs", "destructive": False},
    )])
    h.runner.start("slow task")
    h.wait_plan()
    h.runner.on_plan_answer(True)
    assert started.wait(5)
    h.runner.on_plan_answer(False)   # STOP while executing
    release.set()
    h.wait_done()
    check("step 2 skipped after STOP", ran == [])
    check("stop finish ok=False", h.finished[0][1] is False)


def test_busy_guard_and_cancel_idle():
    release = threading.Event()
    h = Harness({"open_app": lambda a: release.wait(5) or "ok"},
                [plan_json({"tool": "open_app", "args": {}, "title": "Open", "destructive": False})])
    check("cancel with no task", "no task" in h.runner.cancel().lower())
    h.runner.start("first")
    msg = h.runner.start("second")
    check("second task rejected while busy", "already in progress" in msg)
    h.wait_plan()
    h.runner.on_plan_answer(True)
    release.set()
    h.wait_done()


def test_destructive_forced_by_keyword():
    h = Harness({"file_controller": lambda a: "ok"}, [plan_json(
        {"tool": "file_controller", "args": {}, "title": "Delete old logs", "destructive": False},
    )])
    h.runner.start("clean logs")
    h.wait_plan()
    check("keyword forces destructive flag", h.shown[0][1][0]["destructive"] is True)
    h.runner.on_plan_answer(False)
    h.wait_done()


def test_confirmation_pending_wait():
    flags = {"pending": True}
    def tool(a):
        return "[CONFIRMATION_PENDING] shutdown parked on HUD"
    def pending():
        return "Shut down" if flags["pending"] else ""
    h = Harness({"computer_settings": tool},
                [plan_json({"tool": "computer_settings", "args": {},
                            "title": "Shut down the PC", "destructive": True})],
                confirm_pending=pending)
    h.runner.start("shutdown")
    h.wait_plan()
    h.runner.on_plan_answer(True)
    time.sleep(1.0)
    check("agent is waiting on the confirm gate", not h._done.is_set())
    flags["pending"] = False        # user answers the banner
    h.wait_done()
    check("continues after gate resolves", h.finished[0][1] is True)


def test_infeasible_plan():
    h = Harness({"open_app": lambda a: "ok"},
                [json.dumps({"steps": [], "infeasible": "no tool can iron shirts"})])
    h.runner.start("iron my shirt")
    h.wait_done()
    check("infeasible reported", any("[TASK_FAILED]" in s for s in h.says))
    check("runner freed after infeasible", not h.runner.busy)


def test_unknown_tool_in_plan():
    h = Harness({"open_app": lambda a: "ok"},
                [plan_json({"tool": "hack_pentagon", "args": {}, "title": "nope", "destructive": False})])
    h.runner.start("bad plan")
    h.wait_done()
    check("unknown tool rejected", any("unknown tool" in s.lower() for s in h.says))


def test_skip_marks_correct_rows_with_duplicate_steps():
    # Regression: Step has value equality, so steps.index() on two identical
    # steps returned the first row twice. Skips must land on rows 1 AND 2.
    h = Harness(
        {"open_app": lambda a: "Error: nope", "web_search": lambda a: "ok"},
        [
            plan_json(
                {"tool": "open_app", "args": {}, "title": "Open it", "destructive": False},
                {"tool": "web_search", "args": {"query": "x"}, "title": "Search x", "destructive": False},
                {"tool": "web_search", "args": {"query": "x"}, "title": "Search x", "destructive": False},
            ),
            json.dumps({"decision": "abort", "fixed_args": {}, "reason": "dead"}),
        ],
    )
    h.runner.start("dupes")
    h.wait_plan()
    h.runner.on_plan_answer(True)
    h.wait_done()
    skipped_rows = sorted(i for i, s, _ in h.steps if s == "skipped")
    check("both duplicate rows skipped", skipped_rows == [1, 2], str(skipped_rows))


def test_approval_timeout():
    old = agent_mod.APPROVAL_TIMEOUT
    agent_mod.APPROVAL_TIMEOUT = 0.4
    try:
        ran = []
        h = Harness({"open_app": lambda a: ran.append(1) or "ok"},
                    [plan_json({"tool": "open_app", "args": {}, "title": "Open", "destructive": False})])
        h.runner.start("open")
        h.wait_plan()
        h.wait_done(5)
        check("timeout cancels unanswered plan", any("[TASK_CANCELLED]" in s for s in h.says))
        check("nothing ran on timeout", ran == [])
    finally:
        agent_mod.APPROVAL_TIMEOUT = old


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print(f"\nALL {PASS} CHECKS PASSED ({len(tests)} tests)")
