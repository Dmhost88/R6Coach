# R6 Coach — Product Spec (v0.1, 2026-11-06)

An audio-only "coach in your ear" for Rainbow Six Siege on PC. It reads only what is on your own screen and public strategy knowledge. It never shows anything in-game.

## Fair-play rules (hard constraints)

- **Capture only.** Windows Graphics Capture of the game window. No memory reading, no DLL injection, no packet capture, no input automation. Nothing touches the game process, so BattlEye is never involved.
- **HUD and menus only.** It may read the timer, round/score, phase, killfeed, plant/defuse status, your gadget counts, team alive/dead icons, and enemies your team already pinged or spotted by drone or cam.
- **Never** analyses the 3D game world for enemy models, movement or muzzle flashes. It gives no reaction-speed callouts like "enemy left".
- **Knowledge** is what an experienced player could memorise: map layouts, setups, entries, op roles and meta.

## Scope

- **Platform:** PC only, 3440x1440 ultrawide (21:9), English client, default HUD.
- **Playlist:** Ranked only at launch (bomb mode, map ban, op bans).
- **Player:** mostly solo queue, so it adapts to whatever randoms pick. Verbosity and depth are configurable.
- **Audience:** personal use, with your own API keys.

## Coaching moments

1. **Map ban:** keep/ban advice from your comfort and win rates.
2. **Operator bans:** attack/defence ban suggestions for the map and comp.
3. **Operator select:** picks from your preferred pool, owned and not banned. It strays outside the pool only when the comp clearly needs it.
4. **Prep phase:**
   - Defence: reinforcements, rotates, gadget placement, your role on site.
   - Attack: drone path, entry points from your spawn, plant spots, utility to bring.
5. **Action phase:** sparse, event-driven lines such as man-advantage changes, defuser planted or dropped, 45s/20s left, unused gadgets, and recaps of pinged enemies. Non-urgent lines wait until a kill or gunfight event has passed.
6. **Between rounds:** a ~10-second spoken recap with one adjustment.
7. **Post-match:** a written report covering rounds by site/side, deaths by phase and recurring mistakes, saved to history.
8. **Push-to-talk Q&A:** hold a key and ask anything ("where do I plant on Bank basement?"). Speech is transcribed locally with Whisper and answered by the LLM.

## Architecture

| Layer | Choice |
|---|---|
| Capture | Windows Graphics Capture at 2–5 fps, scaled up when state changes. Region crops for HUD elements calibrated to 21:9. |
| Live HUD reading | Local: OpenCV template matching plus fast OCR (e.g. PaddleOCR or RapidOCR) on the GPU. |
| Static screens | Ban, op select and scoreboard screenshots go to a cloud vision LLM. |
| State machine | Phases: menu, map ban, op ban, op select, prep, action, round end, match end. Tracks map, site, side, spawn, ops, score, alive counts and timer. |
| Knowledge | Local versioned database per map/site/side: reinforcements, rotates, entries, plant spots, op roles, ban meta. Seeded by summarising public guides and editable by you. Refreshed each season. |
| Reasoning | Rules engine for instant in-round lines. Claude for op picks, bans, prep plans, debriefs and Q&A. |
| Voice out | Cloud natural TTS. Common lines are pre-generated and cached so they play instantly. Plays through your headset with no ducking. Volume is adjustable. |
| Voice in | Push-to-talk hotkey and local Whisper. |
| Memory | Local SQLite profile: per-map/site/op results and recurring patterns. |
| Recording | Key frames saved per state change, pruned after 14 days. |
| Tone | Calm pro analyst: concise and confident. |
| Shell | Tray app plus a second-monitor dashboard. |
| Stack | Python. Claude builds it; you test in-game. |

The dashboard shows the detected game state, a transcript of coach lines, settings (op pool, chattiness sliders per phase, hotkeys, voice/volume), a map-notes editor, post-match reports and a detection calibration view. It is not an overlay.

## Phases

- **v1 (MVP):** capture, phase detection, map/site/side/op detection, ban and op-select advice, prep-phase setup advice, push-to-talk Q&A, TTS, dashboard basics, knowledge DB for the current Ranked map pool.
- **v2:** live HUD coaching (timer, killfeed, plant/defuse, alive counts, pings) with the chattiness sliders.
- **v3:** between-round debriefs, post-match reports, player-profile learning.

## Inputs needed from you before building

- Anthropic API key and a TTS provider key (ElevenLabs or OpenAI).
- 20–40 screenshots at 3440x1440: map ban, op ban, op select (both sides), prep phase, action phase with killfeed, plant, round end, scoreboard.
- Your preferred op pool per side.
- A push-to-talk key that doesn't clash with your in-game binds.
