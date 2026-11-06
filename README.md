# R6 Coach

An audio-only coach for Rainbow Six Siege Ranked on PC. It watches your screen the way OBS does, works out the match state, and talks you through map bans, operator bans, your operator pick, and the prep phase. Hold a key to ask it anything.

It is designed not to be a cheat. It reads only menus and the HUD from your own screen, plus public strategy knowledge, and never looks for enemies in the game world. See [SPEC.md](SPEC.md).

## Setup

1. Install Python 3.12, then create the environment from this folder:

   ```bash
   python -m venv .venv
   ```

   ```bash
   .venv/Scripts/python -m pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and add your keys:
   - `ANTHROPIC_API_KEY`: reads screens and writes advice.
   - `OPENAI_API_KEY` or `ELEVENLABS_API_KEY`: the coach's voice. Without one, the offline Windows voice is used.
3. Copy `config.example.yaml` to `config.yaml`. Set your operator pool and a push-to-talk key that Siege doesn't use. A spare mouse side button works well: `mouse_x1` or `mouse_x2`.
4. Check the setup:

   ```bash
   .venv/Scripts/python -m r6coach --check
   ```

5. Test the voice:

   ```bash
   .venv/Scripts/python -m r6coach --say "Defuser is down. Play time."
   ```

## Running

```bash
.venv/Scripts/python -m r6coach
```

The coach sits in the system tray and opens the dashboard in your browser at http://127.0.0.1:8765/. Drag that window to your second monitor. Start Siege in **borderless** or windowed mode; window capture is most reliable there.

| Control | What it does |
|---|---|
| Push-to-talk key (hold) | Ask a question out loud |
| Ctrl+Alt+N | "What should I do right now?" |
| Ctrl+Alt+M | Mute / unmute |
| Ctrl+Alt+R | Repeat the last advice |
| Ctrl+Alt+S | Skip the line being spoken |

The dashboard has five tabs:
- **Live**: match state and what the coach said.
- **What the coach sees**: the current frame and the last screen read, for checking detection.
- **Settings**: operator pool, chattiness per phase, skill level, volume.
- **Map notes**: your own notes per map. The coach trusts these over its database.
- **Matches**: recent results.

## Testing without the game

Save screenshots (3440x1440 PNGs) into a folder and replay them as if they were live:

```bash
.venv/Scripts/python -m r6coach --replay path/to/screenshots --replay-seconds 6
```

Run the unit tests (no network needed):

```bash
.venv/Scripts/python -m pytest -q
```

## Live rounds: the local HUD reader

During rounds the coach reads the HUD on your PC with local OCR, about once a second, at no cost:
- the timer, score and round
- the team's reinforcements left
- your room callout and your health
- the centre banners ("PREPARATION PHASE", "5 VS 2", "BOMBS FOUND", "DEFUSER PLANTED", "WON ROUND 3", "ROLE SWAP")
- the killfeed

It uses two CPU threads at most. Regions are measured for 3440x1440; other resolutions are untested.

Live lines are short and pre-recorded so they play instantly:
- timer calls on attack
- reactions to a plant
- man-advantage changes
- your clutch
- a reinforce reminder during defense prep

They wait out a gunfight, respect `coach.chattiness.action_phase` (0–3), and stop when you die. Set `player_name` in `config.yaml` so the coach knows which kills and deaths are yours.

Test it against your own captures:

```bash
.venv/Scripts/python -m pytest -q tests/test_hud.py
```

## Cost

Screens are compared locally first. A screenshot goes to Claude only when the screen changes meaningfully, and never more often than the intervals in `config.yaml` (`vision.min_interval`). The dashboard header shows the running cost. To cut screen-reading cost by roughly 40x, set `vision.model: claude-haiku-5-5`, at some risk to read accuracy.

## What's stored locally

Everything is stored under `data/`:
- `profile.sqlite`: matches, rounds, and what the coach said.
- `frames/`: one screenshot per screen change, deleted after 14 days.
- `tts_cache/`: generated voice lines.
- `notes/`: your map notes.

## Layout

| Path | Purpose |
|---|---|
| `r6coach/capture.py` | Windows Graphics Capture of the game window; replay source |
| `r6coach/watcher.py` | Decides when to read the screen; merges reads into the match state |
| `r6coach/llm.py` | Claude calls with structured outputs |
| `r6coach/coach.py` | When to speak and what to ask Claude for |
| `r6coach/knowledge/` | Map, site and operator data, plus your notes |
| `r6coach/voice/` | Text-to-speech queue and cache; push-to-talk with local Whisper |
| `r6coach/dashboard/` | The second-monitor page |
| `r6coach/profile.py` | SQLite player profile |
