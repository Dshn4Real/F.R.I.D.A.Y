# FRIDAY 4.0


| Area | Tools / features |
|------|------------------|
| Voice | Gemini 3.1 Flash Live (`gemini-3.1-flash-live-preview`), voice picker |
| HUD | PyQt6 reactive waveform, theming, boot animation, memory panel, confirm banner |
| Apps / OS | `open_app`, `computer_settings`, `computer_control`, `desktop_control` |
| Browser | Full `browser_control` (Chrome/Edge/Opera GX/Firefox/…) |
| Files | `file_controller`, `file_processor` (drop zone) |
| Web | `web_search` (news/research/price/compare), `weather_report`, `youtube_video` |
| Vision | `screen_process` (screen + camera) |
| Memory | `save_memory` / `recall_memory` + UI panel |
| Safety | Real HUD confirm (shutdown/restart/WiFi), `undo` stack |
| Extras | reminders, flight finder, game updater, send_message, code_helper, dev_agent |
| Background | system monitor alerts, topic monitors, proactive check-ins, morning briefing |
| Remote | Phone dashboard (FastAPI + QR) |
| Plugins | Drop a `.py` into `plugins/` — includes `hands_board` (local webcam pinch/drag) |

## Agent mode (new in 4.0)

Say a multi-step goal — *"organize my desktop, then take a screenshot and tell
me the CPU usage"* — and FRIDAY calls `run_task` instead of a single tool:

1. **Plan** — a planner model (`core/agent.py`) drafts a step list from the
   real tool catalog (max 8 steps).
2. **Approve** — the full plan appears on the HUD as a checklist; destructive
   steps (delete / send / power) are flagged ⚠. Nothing runs until you press
   **APPROVE & RUN** — the model cannot approve its own plan, same trust model
   as the shutdown confirm banner.
3. **Execute** — steps run in order with live ○ → ▸ → ✔ status on the panel and
   spoken progress. A **STOP** button (or saying "cancel the task") halts
   between steps. Steps that park work behind the confirm banner (shutdown,
   WiFi) wait for your answer instead of racing past it.
4. **Verify** — a result that looks like a failure is reviewed by the model:
   retry once (with corrected arguments), skip, or abort the rest of the plan.

Unanswered plans expire after 2 minutes. One task at a time. Tests:
`python tests/test_agent.py` (36 headless checks, no API key needed).

## Computer use, routines & the kill switch (new in 4.0)

- **`ui_click`** — press any named button/link/menu in any app: finds the
  element through the Windows accessibility tree (pywinauto UIA) and falls back
  to AI vision when the app doesn't expose it. **`verify_screen`** answers a
  question about the current screen so the agent can check its own work.
  The vision finder now uses Gemini's native 0–1000 normalized coordinate
  space and scales through DPI correctly — clicks land where they should.
- **Routines** — *"every weekday at 9, open VS Code and give me the weather"*
  → `manage_routine` stores it in `data/routines.json`; a background loop fires
  it at the right time (10-minute grace window, never twice a day). Routines
  run through the normal tools, so confirms and plan approvals still apply.
  Tests: `python tests/test_routines.py`.
- **Kill switch** — **Ctrl + Alt + K** works system-wide, even while FRIDAY
  controls the mouse: halts the agent task between steps and cuts speech.
  pyautogui's corner-slam FAILSAFE stays on as the zero-software backup.
- **Follow-ups** — *"remind me to follow up with the recruiter if they don't
  reply by Thursday"* stays open and nags on a spaced schedule (1 → 2 → 4 → 7
  days) until you close, drop, or snooze it. Plugin: `follow_up`. Tests:
  `python tests/test_followups.py`.
- **Spotify** — play/pause/skip/volume work immediately via media keys (no
  Premium). Named-track / playlist playback needs a free Spotify developer
  app + later Premium. Plugin: `spotify`.

## Quick start

```powershell
cd c:\Users\disha\OneDrive\Desktop\friday-3.0-main\friday-4.0
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
python main.py
```

On first launch, the setup overlay asks for your **Gemini API key**.
It is stored encrypted in `config/api_keys.json` (AES-256-GCM). The vault
key lives in `%LOCALAPPDATA%\FRIDAY\vault.key`, wrapped with Windows DPAPI —
not in this folder, so OneDrive cannot leak it. Never commit `api_keys.json`.

Optional: set `"preferred_browser": "operagx"` in config (already defaulted).

## Security

Secrets (API keys, Spotify tokens, memory, routines, the dashboard TLS key)
are encrypted at rest with **AES-256-GCM**. The vault key is stored in
`%LOCALAPPDATA%\FRIDAY\vault.key` and wrapped with Windows DPAPI, so copying
this folder (or OneDrive sync to another PC) cannot decrypt it. First launch
migrates leftover plaintext automatically.

This protects stolen/synced files. It does not protect malware running as you.

## Layout

```
friday-4.0/
├── main.py              # Gemini Live loop + tool dispatch
├── ui.py                # PyQt6 HUD
├── actions/             # All Mark 52 skills
├── core/                # prompt, confirm, undo, plugins, audio devices
├── memory/              # long-term memory + config
├── dashboard/           # remote phone control
├── plugins/             # drop-in skills
└── config/api_keys.json # encrypted key + preferences (AES-256-GCM)
```

## Attribution

Core workflow derived from [Mark-LII](https://github.com/FatihMakes/Mark-LII)
(CC BY-NC 4.0). FRIDAY 4.0 is a personalization / hackathon fork for non-commercial use.
