# Development guide

Everything you need to get a working dev setup, change the code safely, and check your change. For how the pieces fit, read [ARCHITECTURE.md](ARCHITECTURE.md). For how we branch and merge, read [CONTRIBUTING.md](../CONTRIBUTING.md).

## Requirements

- **Windows 10/11.** Capture uses the Windows Graphics Capture API and the voice falls back to Windows SAPI. Most of the code runs elsewhere, but only Windows is tested.
- **Python 3.12.**
- An **Anthropic API key** for anything that reads screens or writes advice. The unit tests need no key and no network.
- For real testing: Rainbow Six Siege on PC at **3440×1440**, English client, default HUD, borderless or windowed.

## First-time setup

```bash
git clone https://github.com/Dmhost88/R6Coach.git
```

```bash
cd R6Coach
```

```bash
python -m venv .venv
```

```bash
.venv/Scripts/python -m pip install -r requirements.txt
```

Turn on the repository's git hooks, which stop commits and pushes on `main` (see [CONTRIBUTING.md](../CONTRIBUTING.md#how-the-rules-are-enforced)):

```bash
git config core.hooksPath .githooks
```

Then copy `.env.example` to `.env` (keys) and `config.example.yaml` to `config.yaml` (settings). Both copies are git-ignored. Check the setup:

```bash
.venv/Scripts/python -m r6coach --check
```

Optional, for the live map: download Ubisoft's blueprints once (about 70 MB into `data/maps/`, personal use, never committed):

```bash
.venv/Scripts/python -I tools/fetch_blueprints.py
```

## Running

| Command | What it does |
|---|---|
| `.venv/Scripts/python -m r6coach` | the full app: capture, coach, voice, tray, dashboard at http://127.0.0.1:8765/ |
| `… -m r6coach --replay FOLDER --replay-seconds 6` | plays saved screenshots as if they were live |
| `… -m r6coach --say "text"` | speak one line and exit (voice test) |
| `… -m r6coach --collect` | voice-guided screenshot collection into `data/calibration/` (no API calls) |
| `… -m r6coach --collect --quiet` | record a real match without the spoken checklist |
| `--no-tray`, `--no-ptt`, `--no-browser`, `--port N`, `--offline` | dev switches; see `python -m r6coach --help` |
| `R6 Coach.bat` | what the player double-clicks: background start, logs to `data/coach.log` |

### Working on the dashboard without the game

```bash
.venv/Scripts/python tools/preview_dashboard.py --map Chalet --side defense --site "Master Bedroom" --op Kaid --screen prep_phase --where "2F Library" --port 8766
```

This serves the real dashboard with a made-up state: no capture, no voice, no cloud calls. POST `{"text": "..."}` to `/api/control/ask` to see a coach line and the rooms it names light up on the map. The static files have no build step; reload the page after editing.

Use a port other than 8765 if the real coach is running. If several people or agents are working at once, give each their own port.

## Tests

```bash
.venv/Scripts/python -m pytest -q
```

About 200 tests, about 75 s, no network. Some tests read real captured frames from `data/calibration/` (HUD, screen classifier) and **skip** when those frames are not present, which is the normal state on a fresh clone. `tests/fixtures/opcards/` holds the frames the operator-card tests need, so those always run. The Piper voice test skips until the voice model has been downloaded.

Run a single area while you work:

```bash
.venv/Scripts/python -m pytest -q tests/test_watcher.py
```

Shared fixtures (a small fake Bank map, a `Bus`, a `Knowledge` instance) are in [tests/conftest.py](../tests/conftest.py).

### What to test when you change…

| You changed | Run |
|---|---|
| any Python | the full suite |
| knowledge YAML (setups, layout, maps, callouts) | `tools/check_setups.py` and `tests/test_knowledge.py tests/test_setups.py tests/test_maplayout.py` |
| HUD regions or OCR | `tests/test_hud.py` with real frames present, plus a real match |
| screen classifier | `tests/test_screens.py` and `tools/screens_eval.py` on a capture |
| operator cards | `tests/test_opcards.py` and `tools/opcards_eval.py` |
| prompts or advice logic | `tools/eval_real.py` (costs money, see below) |
| dashboard | `tests/test_dashboard.py` and a look in `tools/preview_dashboard.py` |
| voice | `tests/test_tts.py` and `--say` |

## Evals (real API calls, cost money)

Unit tests check logic. Evals check the quality of what Claude says and reads. Run them before merging any prompt, model or effort change, and paste the before/after scores in the PR.

| Tool | Checks | Cost |
|---|---|---|
| `tools/eval_real.py` | advice on 14 decision points from a real recorded Ranked match (Chalet, won 4–1), scored by a separate Claude judge; scenarios in `tools/real_scenarios.yaml` | about $1 |
| `tools/eval_advice.py [label]` | advice on fixed made-up scenarios, judged | about $0.5–1 |
| `tools/detail_eval.py` | fast pre-round reads (`read_detail`) against saved full reads | depends on frames |
| `tools/screens_eval.py` | local screen classifier accuracy per class (no API) | free |
| `tools/opcards_eval.py` | local operator-card reader on saved frames (no API) | free |

Results go to `data/evals/`. `tests/test_eval_real.py` tests the eval harness itself, offline.

## Debug helpers

- `tools/try_coach.py [effort]`: run the real coach prompts on a made-up state and print the advice.
- `tools/try_stream.py`: time to first streamed line vs full answer.
- `tools/try_vision.py <image> [model]`: one cloud screen read on a saved frame, timed.
- `tools/locate_rooms.py`: locate callout rooms on blueprint images with Claude vision; writes `knowledge/data/layout/<map>.yaml`.
- The dashboard's **What the coach sees** tab shows the current frame and the last read.
- `data/frames/<day>/` keeps one frame per screen change, with the cloud read's JSON beside it.
- `data/coach.log` when started from the .bat file.
- **Debug log:** start with `-v` (`R6 Coach.bat -v`, or `python -m r6coach -v`), or set `R6COACH_DEBUG=1`. It adds millisecond timestamps and thread names, and logs:
  - every bus event (`state.updated` as the fields that changed)
  - every HUD read and local screen change
  - every cloud read with its policy and latency
  - every Claude prompt (the user part, never the system prompt or keys), with the reply and timing
  - for each line, why it was asked, held for a fight, skipped, cut, dropped as a repeat, expired or played

  It runs to about 1 MB per match. Debug is on for the `r6coach` loggers only. Libraries stay at INFO or above, because HTTP clients log headers at debug.

## Code conventions

- Python 3.12, `from __future__ import annotations`, type hints, Pydantic v2 for data shapes.
- Every module starts with a docstring that says what it does and why. Keep that up to date; it is the first thing the next person reads.
- Components get their dependencies from `App` (constructor arguments and callables), and talk through the bus. Don't reach into another component's private fields.
- Bus handlers run on the publisher's thread: keep them fast, start a thread for slow work.
- Claude calls go through `llm.Claude` only, with structured outputs.
- New config: add it to the dataclass in `config.py`, to `config.example.yaml` with a comment, and to the Settings tab if a player would change it.
- Never put secrets in `config.yaml` or code; they live in `.env`.
- Logs: `log = logging.getLogger(__name__)`; no `print` in the app (tools may print).
- Comments explain why, not what.

## Windows notes

- Run `python`, not `python3` (the latter is the Microsoft Store stub).
- Open text files with `encoding="utf-8"`; the Windows default is cp1252.
- Git may warn "LF will be replaced by CRLF". It is harmless, but don't reformat whole files' line endings in a feature PR.
- Stop a stuck dev server by its PID, never by image name (`taskkill /IM python.exe` kills every Python on the machine).

## Releasing to the player

There are no packaged releases yet. The player runs from a clone of `main`: pull, then restart the coach (tray icon → Quit, then `R6 Coach.bat`). That is why `main` must always work; see [CONTRIBUTING.md](../CONTRIBUTING.md).
