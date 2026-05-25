# Runway Automation — Phase 1: Session Recorder

A disposable one-shot data collector. Launches a real Chromium browser, captures every click, input, network request, and WebSocket frame during a manual generation session. The captured data feeds into Phase 2 (the actual automation player) — built later, after we see what real network traffic looks like.

## Requirements

- Windows 10/11
- Python 3.11+ (3.12 recommended)
- ~500 MB disk for the bundled Chromium

## First-time setup

Double-click **`setup.bat`** — it will:

1. Create a `.venv` virtual environment in the project folder
2. Install `playwright`, `playwright-stealth`, `rich`, `loguru`
3. Download a fresh Chromium browser (~170 MB)

Setup runs once. Re-run only if `requirements.txt` changes.

## Recording a session

Double-click **`run_recorder.bat`**.

A real Chromium window opens. Then:

1. Navigate to your generation platform.
2. Log in if needed — cookies are saved to `./browser_profile/` and persist between runs, so you'll only log in once.
3. Perform **one complete generation manually**:
   - upload your reference image
   - paste your prompt
   - set the desired duration
   - press Generate
   - wait for the result
   - download the finished video
4. **Close the browser window** — recording stops automatically and a summary is printed.

The console shows a live counter (`events / network / ws / shots`) so you can confirm capture is working.

## Output

Each session creates a folder under `./recordings/`:

```
recordings/session_2026-04-28_14-30-00/
├── timeline.jsonl            # all click / input / change / keydown / submit events
├── network.jsonl             # all HTTP requests + responses (with bodies)
├── websockets.jsonl          # all WebSocket frames sent + received
├── screenshots/              # PNG after each click
├── cookies_final.json        # final cookie state
├── localstorage_page_*.json  # final localStorage per page
├── metadata.json             # session start info
└── summary.json              # session end stats
```

## What's captured per click

Each click event records a multi-strategy selector for the target element so the replay engine has fallbacks if the DOM changes:

- `id`, `data-testid`, `aria-label`, `role`, `name`, `placeholder`, `type`
- visible text content
- CSS class list
- full XPath

## Project layout (Phase 1)

```
runway_ML/
├── .venv/               # virtual environment (created by setup.bat)
├── browser_profile/     # persistent Chromium profile (cookies, storage)
├── recordings/          # captured sessions
├── recorder.py          # the recorder
├── requirements.txt
├── setup.bat
├── run_recorder.bat
├── .gitignore
└── README.md
```

## Next phase

After one or two recordings, we'll inspect the JSONL output, identify the generation API endpoints and DOM flow, then build Phase 2 — the desktop app (Flet UI) that replays the flow with batch inputs and concurrency=2.
