# Architecture

How R6 Coach is put together: what runs, in which thread, how data moves, and where each decision is made. Read [SPEC.md](../SPEC.md) first for what the product is and the fair-play rules. This page is about how it is built.

## The one-paragraph version

A capture thread pulls frames of the Siege window from Windows. Two local readers look at each frame for free: the **screen classifier** works out which screen is up (menu, ban, pick, prep, live round, scoreboard), and the **HUD reader** reads the timer, score, banners, killfeed and your room during rounds. The **watcher** decides when a frame is worth a paid cloud read by Claude (bans, picks, the first prep read, the scoreboard), merges every read into one `GameState`, and publishes events on a small in-process **bus**. The **coach**, the **live coach** (rule-based, instant) and the **tactical coach** (Claude, in-round) listen to those events and decide what to say. The **speaker** turns lines into audio through a cached, prioritised queue. A **dashboard** (FastAPI on 127.0.0.1) mirrors all of it on a second monitor.

## Data flow

```
 Siege window
     │  Windows Graphics Capture (capture.py) — receives finished frames only
     ▼
 FrameSource ──────────────┬───────────────────────────────┐
     │                     │                               │
     ▼                     ▼                               ▼
 Watcher (watcher.py)   HudThread (hud.py)            Dashboard /api/frame.jpg
  ├ thumbnail diff       ├ timer, score, round
  ├ ScreenClassifier     ├ banners ("5 VS 2", "DEFUSER PLANTED")
  │  (screens.py)        ├ killfeed, your room, your health
  ├ OperatorCardReader   └─► watcher.apply_hud()
  │  (opcards.py)
  ├ ReadGate: cloud read or not?
  └ Claude.read_screen / read_detail (llm.py)
     │
     ▼
 GameState (models.py) ──► bus: state.updated, screen.changed, match.started,
                                round.ended, player.died, killfeed, match.ended …
     │
     ├──► Coach (coach.py)          bans, picks, prep plan, debriefs, push-to-talk answers
     ├──► LiveCoach (live.py)       fixed instant lines from HUD events (no API)
     ├──► TacticalCoach (tactical.py) one Claude call per key in-round moment
     ├──► MatchMemory (memory.py)   what happened earlier this match
     └──► Profile (profile.py)      SQLite history
                │
                ▼
          Speaker (voice/tts.py) ──► headset
          bus: coach.said ──► App._map_focus ──► map.focus ──► dashboard live map
```

`App` in [r6coach/app.py](../r6coach/app.py) builds every component and wires them together. It is the best single file to read first.

## Threads

| Thread | Owner | What it does |
|---|---|---|
| main | `__main__.py` | parses args, starts `App`, runs the tray icon (`tray.py`) or waits for Ctrl+C |
| capture | `capture.WindowCaptureSource` | receives frames from Windows; keeps the latest |
| watcher | `watcher.Watcher` | samples frames at `capture.sample_fps`, classifies, gates cloud reads; cloud reads run async (`watcher.async_reads = True`) |
| HUD | `hud.HudThread` | local OCR about once a second (`hud.period`), two CPU threads at most |
| speaker | `voice.tts.Speaker` | priority queue; synthesises, caches, plays |
| PTT | `voice.ptt.PushToTalk` | key listener (observe only) and local Whisper transcription |
| dashboard | uvicorn in `dashboard/server.py` | HTTP + one websocket that forwards bus events |
| Claude calls | `llm.Claude` | short-lived worker threads per request, streaming where the first line matters |

Components talk through the bus or through the callables `App` hands them (`current_state=lambda: self.watcher.state`). They do not import each other's internals.

## The bus

[r6coach/bus.py](../r6coach/bus.py) is a tiny thread-safe publish/subscribe bus. Handlers run on the publisher's thread, so keep them short; anything slow (a Claude call) starts its own thread.

| Event | Published by | Meaning |
|---|---|---|
| `state.updated` | watcher | `GameState` changed; carries `state` as JSON |
| `screen.changed` | watcher | the screen type changed (e.g. `operator_select` → `prep_phase`) |
| `vision.read` | watcher | a cloud screen read came back |
| `match.started` / `match.ended` | watcher | match boundaries; resets match cost and memory |
| `round.ended` | watcher | round result known |
| `player.died` | watcher / memory | your death, from the killfeed |
| `killfeed` | watcher | new killfeed rows |
| `memory.round` | memory | a round was added to match memory |
| `live.event` | watcher / live | HUD event (plant, count change, timer marks) |
| `coach.thinking` | coach | a Claude call is running (dashboard spinner) |
| `coach.advice` | coach | structured advice for the dashboard |
| `coach.said` | speaker / coach | a line was spoken; drives the live map focus |
| `map.focus` | app | which rooms the last line named, for the live map |
| `ptt.listening` / `ptt.question` / `ptt.ready` | ptt | push-to-talk state and transcribed questions |
| `status` | app | mute/pause/cost changes |
| `error` | anywhere | logged as a warning and shown on the dashboard |

When you add an event, add it to this table.

## Game state

[r6coach/models.py](../r6coach/models.py) defines:

- `Screen`: `main_menu`, `matchmaking`, `map_ban`, `operator_ban`, `operator_select`, `loadout`, `prep_phase`, `action_phase`, `round_end`, `match_end`, `scoreboard`, `spectating`, `loading`, `other`.
- `ScreenRead`: what one cloud read extracts from one screenshot. **There is deliberately no field for enemy positions seen in the 3D world.** Do not add one.
- `GameState`: the merged view (map, site, side, round, score, your operator, teammates, bans, timer, plant status, alive counts, your room…).

[r6coach/twin.py](../r6coach/twin.py) renders `GameState` into the one text block every prompt sees. Each fact is tagged `READ` (seen on screen), `PREDICTED` (worked out, e.g. side from the swap rule in [rounds.py](../r6coach/rounds.py)) or unknown, so the model can weigh it. Your locked operator is kept apart from what the coach suggested.

## Cost control (why there are three readers)

Cloud screen reads cost about $0.012 each and take 3–8 s, so the app avoids them:

1. **Thumbnail diff** (`watcher.thumbnail`/`diff`): tiny grayscale thumbnails; unchanged screens are ignored.
2. **Local screen classifier** ([screens.py](../r6coach/screens.py)): pixel checks plus recognition-only OCR on fixed title boxes tell screens apart in ~10–20 ms. `local_cloud_policy` then says whether the cloud adds anything for that screen. This cut cloud reads by about 68%.
3. **Local operator cards** ([opcards.py](../r6coach/opcards.py)): reads your pick, teammates' picks and bans in ~100 ms, so the coach knows your lock before the cloud read lands.
4. **Local HUD reader** ([hud.py](../r6coach/hud.py)): everything in-round. Live rounds need almost no cloud reads (`vision.min_interval.action_phase: 90`).
5. **`ReadGate`** enforces `vision.min_interval` per screen.
6. **Per-match budget** (`coach.budget_per_match`, USD): at 80% live calls and debriefs pause; at 100% only bans and picks run. The dashboard header shows the running cost.

All HUD/screen regions are measured on 3440×1440 and stored **relative to the screen centre in units of screen height**, which is how Siege lays out its HUD. Other 21:9 sizes should land in the same place; 16:9 is unverified.

## Claude calls

[r6coach/llm.py](../r6coach/llm.py) is the only module that talks to the Anthropic API.

- Every call uses **structured outputs** with Pydantic models (`Wire*`, `CoachLines`), so nothing else parses free text.
- Requests opt into **server-side refusal fallbacks** (`FALLBACK_BETA`), so a rare false-positive decline is retried on another model instead of leaving the coach silent.
- Models are set in `config.yaml`: `vision.model` (full screen reads), `vision.detail_model` (fast ban/pick reads), `coach.model` (advice), `coach.fast_model` (bans, picks, in-round calls). Efforts are per job (`effort`, `pick_effort`, `deep_effort`).
- Advice streams; the first sentence is spoken while the rest is still being written (`_take_stream_line`).

## Knowledge

[r6coach/knowledge/](../r6coach/knowledge/) holds versioned YAML: per-map sites and plans, callouts, blueprint room boxes, setup briefs, operators and meta. The player's own notes (`data/notes/`) override the database. Formats and how to edit them: [KNOWLEDGE_DATA.md](KNOWLEDGE_DATA.md).

## Voice

[r6coach/voice/tts.py](../r6coach/voice/tts.py): a priority queue with a WAV cache. Providers are tried in order: the configured one, then local Piper, then Windows SAPI, so the coach is never silent. Fixed live lines are pre-generated so they play with no delay. [voice/ptt.py](../r6coach/voice/ptt.py) transcribes push-to-talk locally with faster-whisper; audio never leaves the PC.

## Dashboard

[r6coach/dashboard/server.py](../r6coach/dashboard/server.py) is FastAPI bound to `127.0.0.1`. Static files are in `dashboard/static/` (`index.html`, `js/app.js`, `js/livemap.js`, `css/app.css`, design tokens and fonts in `ds/`). There is no build step. The page gets live updates over `/ws` and reads everything else from `/api/*`:

- `/api/status`, `/api/setup`, `/api/memory`, `/api/history`, `/api/frame.jpg`
- `/api/settings` (GET/POST) — writes `config.yaml`
- `/api/maps`, `/api/maps/{id}`, `/api/maps/{id}/notes`
- `/api/layout`, `/api/blueprint/{map}/{image}` — the live map
- `/api/setups`, `/api/setups/{map}/shot/{slot}` (+ `/frame`, `/grab`, `/marks`) — setup briefs and the player's own pictures
- `/api/frames`, `/api/matches`, `/api/control/{action}`

Secrets never reach the page: `tests/test_dashboard.py` checks that an API key in the environment does not leak into any response.

## Storage

Everything the app writes is under `data/` (git-ignored): `profile.sqlite`, `frames/` (pruned after `storage.keep_frames_days`), `tts_cache/`, `notes/`, `models/` (voice models), `maps/` (Ubisoft blueprints, personal use), `setups/` (your pictures), `calibration/` (captured screenshots), `evals/`, `coach.log`.

## Invariants to keep

These are design decisions, not accidents. Changing one needs a conversation with the project owner first.

1. **Capture only.** No memory reading, injection, packet capture or input automation. Hotkey listeners observe; they never send input.
2. **HUD and menus only.** Nothing analyses the 3D world for enemies. No "enemy left" style callouts. `ScreenRead` has no enemy-position field.
3. **Local first.** A cloud read must be justified by `local_cloud_policy`/`ReadGate`.
4. **Structured outputs only** from Claude.
5. **The dashboard binds to 127.0.0.1** and never returns secrets.
6. **Ubisoft blueprint images are never committed.**
