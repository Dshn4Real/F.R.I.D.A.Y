"""
core/agent.py — the agent loop: plan → approve → execute → verify.

WHY THIS EXISTS
    Every tool FRIDAY has fires one action per voice command. That is fine for
    "open Chrome", useless for "organize my downloads, then email me a summary".
    This module turns a spoken *goal* into a step list, shows the whole list on
    the HUD for ONE human approval, then executes the steps sequentially —
    checking each result before moving to the next.

THE TRUST MODEL (matches core/confirm.py)
    The approval comes from the *interface*, never from the model:

      1. The Live model calls run_task(goal). We return immediately — the model
         keeps talking while a planner model drafts the step list.
      2. The full plan appears on the HUD with APPROVE / CANCEL. Destructive
         steps are flagged in red so the user sees them BEFORE anything runs.
      3. Only a button press starts execution. While running, the panel shows
         live step status and a STOP button that halts between steps.
      4. Steps that are irreversible on their own (shutdown, wifi off) still go
         through core/confirm.py — this module waits for that gate to resolve
         instead of racing past it.

VERIFICATION
    After each step the result string is inspected. A result that smells like a
    failure is handed back to the planner model with the goal and the remaining
    plan; it answers retry / skip / abort (with fixed arguments for a retry).
    One retry per step, then the reflection's verdict stands. This is the
    difference between an agent and a macro: a macro plays the tape to the end,
    an agent notices the tape is wrong.

THREADING
    Everything here runs on its own daemon threads — planning and execution
    both block on network and on tools that click around the screen. The UI is
    only ever touched through the injected callbacks, which the FridayUI layer
    marshals onto the Qt thread via signals. `say()` injects a short
    instruction into the Live session so FRIDAY narrates progress out loud.
"""

from __future__ import annotations

import json
import re
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# Tried in order. Observed live: flash throws sustained 503 UNAVAILABLE spikes
# under load — lite runs on separate capacity and plans fine, so a saturated
# primary degrades the planner instead of failing the whole task.
PLANNER_MODELS   = ("gemini-flash-latest", "gemini-flash-lite-latest")
MAX_STEPS        = 8       # a plan longer than this is a project, not a task
STEP_TIMEOUT     = 240.0   # seconds a single tool call may run before we abandon it
APPROVAL_TIMEOUT = 120.0   # unanswered plan is auto-cancelled (mirrors confirm.py)
CONFIRM_WAIT     = 95.0    # max wait for the user to answer an inner confirm gate

# Results that look like the tool did NOT do what the step wanted.
_FAIL_RE = re.compile(
    r"\b(fail|failed|failure|error|unable|cannot|can[' ]?t|couldn[' ]?t|not found|"
    r"no such|denied|timeout|timed out|refused|missing|invalid)\b",
    re.IGNORECASE,
)

# Belt-and-braces destructive detection: the planner is *asked* to flag these,
# but a keyword match forces the flag on even if the model forgets.
_DESTRUCTIVE_RE = re.compile(
    r"\b(delete|remove|uninstall|shutdown|shut down|restart|reboot|format|"
    r"send|pay|purchase|buy|order|kill|terminate|overwrite|wipe|erase|"
    r"empty|clear|disable|turn off)\b",
    re.IGNORECASE,
)

_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$", re.MULTILINE)


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", (text or "").strip()).strip()


def _parse_json(text: str) -> Optional[dict]:
    """Model JSON arrives fenced, bare, or wrapped in prose. Take the first
    balanced object we can find; None if there isn't one."""
    raw = _strip_fences(text)
    try:
        return json.loads(raw)
    except Exception:
        pass
    start = raw.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(raw)):
        if raw[i] == "{":
            depth += 1
        elif raw[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(raw[start:i + 1])
                except Exception:
                    return None
    return None


@dataclass
class Step:
    tool:        str
    args:        dict
    title:       str
    destructive: bool = False
    status:      str  = "pending"   # pending | running | done | failed | skipped
    result:      str  = ""
    retried:     bool = False


@dataclass
class _Task:
    goal:     str
    steps:    list = field(default_factory=list)
    approved: threading.Event = field(default_factory=threading.Event)
    answered: threading.Event = field(default_factory=threading.Event)
    accept:   bool = False
    stop:     threading.Event = field(default_factory=threading.Event)


class AgentRunner:
    """One instance per process, owned by FridayLive. One task at a time —
    a personal assistant juggling two half-approved plans is a hazard, not a
    feature."""

    def __init__(
        self,
        tools: dict[str, Callable[[dict], str]],
        tool_declarations: list[dict],
        get_api_key: Callable[[], str],
        say: Callable[[str], None],
        log: Callable[[str], None],
        ui_show_plan: Callable[[str, list], None],
        ui_update_step: Callable[[int, str, str], None],
        ui_finish_plan: Callable[[str, bool], None],
        ui_hide_plan: Callable[[], None],
        confirm_pending: Callable[[], str],
    ):
        self._tools        = tools
        self._decls        = [d for d in tool_declarations if d["name"] in tools]
        self._get_api_key  = get_api_key
        self._say          = say
        self._log          = log
        self._ui_show      = ui_show_plan
        self._ui_step      = ui_update_step
        self._ui_finish    = ui_finish_plan
        self._ui_hide      = ui_hide_plan
        self._confirm_pending = confirm_pending
        self._task: Optional[_Task] = None
        self._lock = threading.Lock()

    # ── Public surface (called from the tool dispatcher & the UI) ─────────────

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._task is not None

    def start(self, goal: str, context: str = "") -> str:
        """Called by the run_task tool. Returns instantly with the sentence the
        Live model should speak; planning continues on a worker thread."""
        goal = (goal or "").strip()
        if not goal:
            return "No goal was given, so there is nothing to plan."

        with self._lock:
            if self._task is not None:
                return (
                    f"A task is already in progress: {self._task.goal[:80]}. "
                    f"Tell the user one task runs at a time — they can say "
                    f"'cancel the task' first."
                )
            self._task = _Task(goal=goal)

        threading.Thread(
            target=self._plan_and_wait, args=(self._task, context),
            daemon=True, name="agent-plan",
        ).start()

        return (
            "[TASK_PLANNING] I am drafting a step-by-step plan for this task. "
            "Say ONE short sentence in the user's own language telling them you "
            "are preparing a plan they will need to approve on screen. "
            "Do NOT claim any step is done yet."
        )

    def cancel(self) -> str:
        """Called by the cancel_task tool or externally."""
        with self._lock:
            t = self._task
        if t is None:
            return "There is no task running."
        t.stop.set()
        t.accept = False
        t.answered.set()   # unblock a plan still waiting for approval
        self._log("SYS: Task cancel requested.")
        return ("Cancellation requested. The current step finishes, then the "
                "task halts. Tell the user briefly.")

    def on_plan_answer(self, accepted: bool) -> None:
        """Wired to the HUD plan panel's APPROVE / CANCEL / STOP buttons."""
        with self._lock:
            t = self._task
        if t is None:
            return
        if t.answered.is_set():
            # Panel is in execution mode — the button now means STOP.
            t.stop.set()
            self._log("SYS: STOP pressed — halting after current step.")
            return
        t.accept = bool(accepted)
        t.answered.set()

    def status_text(self) -> str:
        with self._lock:
            t = self._task
        if t is None:
            return "No task is running."
        if not t.steps:
            return f"Task '{t.goal[:60]}' is still being planned."
        if not t.answered.is_set():
            return f"Task '{t.goal[:60]}' is waiting for approval on the HUD."
        done = sum(1 for s in t.steps if s.status in ("done", "skipped"))
        return f"Task '{t.goal[:60]}': step {min(done + 1, len(t.steps))} of {len(t.steps)}."

    # ── Planner ───────────────────────────────────────────────────────────────

    def _catalog(self) -> str:
        lines = []
        for d in self._decls:
            props = (d.get("parameters") or {}).get("properties") or {}
            args = ", ".join(
                f"{k}: {v.get('type', 'STRING')} — {v.get('description', '')[:80]}"
                for k, v in props.items()
            )
            lines.append(f"- {d['name']}({args})\n  {d.get('description', '')[:220]}")
        return "\n".join(lines)

    def _llm(self, prompt: str) -> str:
        """One planner/reflection call, resilient to capacity spikes.

        Per model: two attempts with backoff on transient errors (503/429).
        If the primary model stays saturated, fall through to the next model
        in PLANNER_MODELS rather than failing the task — a degraded planner
        beats no planner. Permanent errors (bad key, bad request) raise
        immediately: retrying those only delays the real message."""
        from google import genai
        client = genai.Client(api_key=self._get_api_key())
        last: Exception | None = None
        for model in PLANNER_MODELS:
            for attempt in range(2):
                try:
                    resp = client.models.generate_content(
                        model=model,
                        contents=prompt,
                        config={"response_mime_type": "application/json"},
                    )
                    return (getattr(resp, "text", None) or "").strip()
                except Exception as e:
                    last = e
                    transient = any(tok in str(e) for tok in
                                    ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED",
                                     "overloaded", "high demand"))
                    if not transient:
                        raise
                    if attempt == 0:
                        time.sleep(1.5)
            self._log(f"SYS: Planner model {model} overloaded — trying fallback.")
        raise last if last else RuntimeError("planner failed with no error recorded")

    def _make_plan(self, goal: str, context: str) -> tuple[list, str]:
        """Returns (steps, error). Empty steps + message when infeasible."""
        prompt = (
            "You are the task planner inside FRIDAY, a Windows desktop assistant "
            "with full control of the user's computer. Break the user's goal into "
            f"the SHORTEST sequence of tool calls that completes it (max {MAX_STEPS} steps).\n\n"
            f"AVAILABLE TOOLS:\n{self._catalog()}\n\n"
            f"USER'S GOAL: {goal}\n"
            + (f"CONTEXT: {context}\n" if context else "")
            + "\nRules:\n"
            "- Use ONLY the tools listed. Argument names must match exactly.\n"
            "- One tool call per step. Steps run strictly in order.\n"
            "- Mark destructive:true on any step that deletes, sends, closes, "
            "purchases, or changes power/network state.\n"
            "- title: 5-9 plain words the user reads on screen, present tense.\n"
            "- If the goal cannot be done with these tools, return steps:[] and "
            "explain in 'infeasible'.\n\n"
            "Reply with ONLY this JSON:\n"
            '{"steps": [{"tool": "...", "args": {...}, "title": "...", '
            '"destructive": false}], "infeasible": ""}'
        )
        try:
            data = _parse_json(self._llm(prompt))
        except Exception as e:
            msg = str(e)
            if any(tok in msg for tok in ("503", "429", "UNAVAILABLE",
                                          "RESOURCE_EXHAUSTED", "overloaded")):
                # The raw error is a JSON blob; the user needs one sentence.
                return [], ("Gemini is temporarily overloaded on all planner "
                            "models. The task can simply be asked again in a "
                            "minute or two.")
            return [], f"The planner is unreachable: {msg[:160]}"
        if not isinstance(data, dict):
            return [], "The planner returned something that was not a plan."

        raw = data.get("steps") or []
        if not raw:
            return [], data.get("infeasible") or "This goal needs tools I do not have."

        steps: list[Step] = []
        for s in raw[:MAX_STEPS]:
            tool = str(s.get("tool", "")).strip()
            if tool not in self._tools:
                return [], f"The plan used an unknown tool: {tool}."
            args  = s.get("args") if isinstance(s.get("args"), dict) else {}
            title = str(s.get("title") or tool).strip()[:90]
            destructive = bool(s.get("destructive")) or bool(_DESTRUCTIVE_RE.search(title))
            steps.append(Step(tool=tool, args=args, title=title, destructive=destructive))
        return steps, ""

    # ── Reflection (verify a suspicious step result) ──────────────────────────

    def _reflect(self, task: _Task, idx: int) -> dict:
        step = task.steps[idx]
        remaining = [s.title for s in task.steps[idx + 1:]]
        prompt = (
            "You supervise a desktop-automation task. A step just returned a "
            "result that may mean it failed. Decide what to do.\n\n"
            f"GOAL: {task.goal}\n"
            f"STEP: {step.title}  (tool={step.tool}, args={json.dumps(step.args)[:400]})\n"
            f"RESULT: {str(step.result)[:600]}\n"
            f"REMAINING STEPS: {remaining}\n"
            f"ALREADY RETRIED: {step.retried}\n\n"
            "Options:\n"
            "- continue: the result is actually fine, or good enough.\n"
            "- retry: try once more, optionally with corrected args (fixed_args).\n"
            "- skip: this step failed but the rest of the plan still makes sense.\n"
            "- abort: without this step the task cannot succeed.\n\n"
            "Reply with ONLY this JSON:\n"
            '{"decision": "continue|retry|skip|abort", "fixed_args": {}, "reason": "..."}'
        )
        try:
            data = _parse_json(self._llm(prompt)) or {}
        except Exception:
            data = {}
        decision = str(data.get("decision", "")).lower()
        if decision not in ("continue", "retry", "skip", "abort"):
            # Reflection unavailable → be conservative: keep going on the
            # original result rather than inventing a retry loop.
            decision = "continue"
        if decision == "retry" and step.retried:
            decision = "skip"
        return {
            "decision":   decision,
            "fixed_args": data.get("fixed_args") if isinstance(data.get("fixed_args"), dict) else {},
            "reason":     str(data.get("reason", ""))[:200],
        }

    # ── Lifecycle threads ─────────────────────────────────────────────────────

    def _plan_and_wait(self, task: _Task, context: str) -> None:
        try:
            steps, err = self._make_plan(task.goal, context)
        except Exception as e:
            steps, err = [], f"Planning crashed: {e}"
            traceback.print_exc()

        if not steps:
            self._log(f"SYS: Task planning failed — {err}")
            self._say(
                f"[TASK_FAILED] Planning failed: {err} Tell the user in one "
                f"short sentence, in their language."
            )
            self._clear(task)
            return

        task.steps = steps
        ui_steps = [
            {"title": s.title, "tool": s.tool, "destructive": s.destructive}
            for s in steps
        ]
        try:
            self._ui_show(task.goal, ui_steps)
        except Exception as e:
            self._log(f"ERR: Plan panel failed — {e}")
            self._say("[TASK_FAILED] The plan could not be shown on screen, so "
                      "nothing was run. Tell the user briefly.")
            self._clear(task)
            return

        n_danger = sum(1 for s in steps if s.destructive)
        self._log(f"SYS: Plan ready — {len(steps)} steps"
                  + (f", {n_danger} flagged destructive" if n_danger else ""))
        self._say(
            f"[TASK_PLAN_READY] The plan is on screen: {len(steps)} steps"
            + (f", {n_danger} of them destructive (flagged in red)" if n_danger else "")
            + ". Say ONE short sentence asking the user to review and approve it "
              "on the HUD. Do not read every step aloud."
        )

        if not task.answered.wait(APPROVAL_TIMEOUT):
            self._log("SYS: Plan approval timed out — cancelled.")
            self._ui_hide()
            self._say("[TASK_CANCELLED] The plan expired without approval. "
                      "Mention it briefly.")
            self._clear(task)
            return

        if not task.accept or task.stop.is_set():
            self._log("SYS: Plan rejected by user.")
            self._ui_hide()
            self._say("[TASK_CANCELLED] The user declined the plan on screen. "
                      "Acknowledge briefly and do nothing else.")
            self._clear(task)
            return

        self._execute(task)

    def _run_step(self, task: _Task, idx: int) -> None:
        """Run one tool call with a hard timeout. The tool runs on a scratch
        thread; if it hangs past STEP_TIMEOUT we abandon the thread (daemon)
        and record a timeout rather than freezing the whole task forever."""
        step = task.steps[idx]
        fn = self._tools[step.tool]
        box: dict[str, Any] = {}

        def _call():
            try:
                box["result"] = fn(dict(step.args)) or "Done."
            except Exception as e:
                box["error"] = f"{type(e).__name__}: {e}"

        worker = threading.Thread(target=_call, daemon=True, name=f"agent-step-{idx}")
        worker.start()
        worker.join(STEP_TIMEOUT)

        if worker.is_alive():
            step.result = f"Step timed out after {int(STEP_TIMEOUT)} seconds."
            step.status = "failed"
        elif "error" in box:
            step.result = f"Tool raised an error: {box['error']}"
            step.status = "failed"
        else:
            step.result = str(box.get("result", "Done."))
            step.status = "done"

        # An action may have parked its real work behind the confirm gate
        # (core/confirm.py). Wait for the human to answer that banner before
        # judging the step or moving on — racing past a pending shutdown
        # confirmation would put two questions on screen at once.
        if "[CONFIRMATION_PENDING]" in step.result:
            self._ui_step(idx, "running", "waiting for your confirmation…")
            deadline = time.monotonic() + CONFIRM_WAIT
            while time.monotonic() < deadline and not task.stop.is_set():
                if not self._confirm_pending():
                    break
                time.sleep(0.5)
            step.result = "Waited for the on-screen confirmation to be answered."
            step.status = "done"

    def _execute(self, task: _Task) -> None:
        self._log(f"SYS: Task started — {task.goal[:70]}")
        aborted = ""

        def _skip_from(start: int) -> None:
            # NOT steps.index(step): Step is a value-equality dataclass, so two
            # identical steps would both resolve to the first one's row.
            for j in range(start, len(task.steps)):
                task.steps[j].status = "skipped"
                self._ui_step(j, "skipped", "")

        for idx, step in enumerate(task.steps):
            if task.stop.is_set():
                aborted = "stopped by the user"
                _skip_from(idx)
                break

            self._ui_step(idx, "running", "")
            self._log(f"SYS: Step {idx + 1}/{len(task.steps)} — {step.title}")
            self._run_step(task, idx)

            # Verify: reflection only when the result smells wrong. Reflecting
            # on every step would double latency for zero information on the
            # happy path.
            if step.status == "failed" or _FAIL_RE.search(step.result or ""):
                verdict = self._reflect(task, idx)
                self._log(f"SYS: Verify step {idx + 1} → {verdict['decision']}"
                          + (f" ({verdict['reason']})" if verdict["reason"] else ""))

                if verdict["decision"] == "retry" and not task.stop.is_set():
                    step.retried = True
                    if verdict["fixed_args"]:
                        step.args = verdict["fixed_args"]
                    self._ui_step(idx, "running", "retrying…")
                    self._run_step(task, idx)
                    if step.status == "failed" or _FAIL_RE.search(step.result or ""):
                        second = self._reflect(task, idx)
                        if second["decision"] == "abort":
                            step.status = "failed"
                            self._ui_step(idx, "failed", step.result[:80])
                            aborted = second["reason"] or "a step kept failing"
                            _skip_from(idx + 1)
                            break
                        step.status = "done" if second["decision"] == "continue" else "skipped"
                elif verdict["decision"] == "skip":
                    step.status = "skipped"
                elif verdict["decision"] == "abort":
                    step.status = "failed"
                    self._ui_step(idx, "failed", step.result[:80])
                    aborted = verdict["reason"] or "a required step failed"
                    _skip_from(idx + 1)
                    break
                else:
                    step.status = "done"

            self._ui_step(idx, step.status, step.result[:80])

        # ── Wrap up ──────────────────────────────────────────────────────────
        done    = sum(1 for s in task.steps if s.status == "done")
        skipped = sum(1 for s in task.steps if s.status == "skipped")
        ok      = not aborted and done > 0

        if aborted:
            summary = f"Task halted: {aborted}. {done} of {len(task.steps)} steps completed."
        elif skipped:
            summary = f"Task finished with gaps: {done} steps done, {skipped} skipped."
        else:
            summary = f"All {done} steps completed."

        self._log(f"SYS: {summary}")
        self._ui_finish(summary, ok)

        results = "; ".join(
            f"{s.title} → {s.status}" for s in task.steps
        )[:800]
        self._say(
            f"[TASK_DONE] {summary} Step outcomes: {results}. "
            f"Give the user a natural one-or-two-sentence spoken summary in "
            f"their own language — what was achieved and anything that failed. "
            f"Do not list every step mechanically."
        )
        self._clear(task)

    def _clear(self, task: _Task) -> None:
        with self._lock:
            if self._task is task:
                self._task = None
