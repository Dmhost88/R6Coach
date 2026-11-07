# Handover: where the project stands

Written 2026-11-06 for whoever picks up development next. It covers what works, what is fragile, what is planned, and where to start. Keep it current: update it in the same PR as any change that makes a line here untrue.

## What R6 Coach is

An audio-only coach for Rainbow Six Siege Ranked on PC. It captures your own screen the way OBS does, works out the match state, and talks you through map bans, operator bans, your pick, prep and the round, and answers push-to-talk questions. It never reads game memory, never sends input, and never looks for enemies in the 3D world. Product and fair-play rules: [SPEC.md](../SPEC.md).

It was built quickly (the history starts on 2026-11-06) by the project owner testing in real Ranked matches, with Claude writing the code. The owner is the only user so far.

## Read in this order

1. [SPEC.md](../SPEC.md): what and why, and the fair-play rules (not negotiable).
2. [README.md](../README.md): what the player does.
3. [docs/ARCHITECTURE.md](ARCHITECTURE.md): components, threads, bus events, cost control, invariants.
4. [docs/DEVELOPMENT.md](DEVELOPMENT.md): setup, running without the game, tests, evals.
5. [docs/KNOWLEDGE_DATA.md](KNOWLEDGE_DATA.md): the YAML strategy data.
6. [CONTRIBUTING.md](../CONTRIBUTING.md): branches, PRs, merging.
7. [r6coach/app.py](../r6coach/app.py): the wiring, in 170 lines.

## Status against the spec

| Phase | Scope | Status |
|---|---|---|
| v1 | capture, phase detection, map/site/side/op detection, ban and pick advice, prep advice, push-to-talk Q&A, voice, dashboard, knowledge for the Ranked pool | **Done**, used in real matches |
| v2 | live HUD coaching: timer, killfeed, plant/defuse, alive counts, chattiness sliders | **Done**: local HUD reader (`hud.py`), instant rule lines (`live.py`), Claude in-round calls (`tactical.py`) |
| v3 | between-round debriefs, post-match report, player-profile learning | **Mostly done**: debriefs, death debriefs, post-match report with takeaways, personal stats (site record, rooms you die in, first-blood rate, operator records) feed the prompts. Deeper pattern mining of the profile is not started. |
| extra | live map on blueprints, standard setup briefs per site with the player's own pictures, a Setups tab to browse them by map, site, side and operator | **Done** for all 15 Ranked maps; room positions are machine-located and only partly hand-checked |

Measured so far: the real-match eval (`tools/eval_real.py`, 14 decision points from a won 4–1 Chalet match) scored overall 3.88/5, grounded 3.71/5 after the last prompt work. Local screen recognition cut cloud reads on a replayed match from 104 to 33.

## Known limits and fragile spots

- **One screen size.** Every HUD, screen and card region is measured on 3440×1440. Regions are centre-relative in screen-height units, so other 21:9 sizes should work; 16:9 and other aspect ratios are untested and likely wrong at the edges (killfeed, top bar).
- **English client, default HUD.** OCR matches English words.
- **Ranked only.** Other playlists (Quick Match, Unranked, Arcade) are not recognised as such.
- **Screen classifier accuracy is optimistic.** 99.8% on 539 frames, but tuned on the same frames. Expect misses on screens not in that capture (new season UI, events).
- **Room boxes on the live map** were located by Claude vision with confidences of 0.5–0.7 on many rooms. Expect some to be off; fix by hand in `knowledge/data/layout/`.
- **Layouts lag map reworks.** The attack briefs were rebuilt from Garfieldisdoc's 2026 bombsite guide (2026-11-07), whose in-game HUD names rooms our layouts lack. The worst cases are Oregon (Game Room, Walk In, Boiler Room, Supply Closet, Laundry Storage; "Big Tower" where the layout says "Small Tower"), Outback (Motel Balcony, Back Stairs, Party Hall, Bike Repair), Calypso Casino, Fortress and Villa 1F (no rooms). Spots there use `at:` estimates, so map icons can be off while the step text is right.
- **Setup pictures from the guide video stay local.** Stills for the attack steps (about 325, with marks) were pulled from that video into `data/setups/<map>/`, which is git-ignored; they are not in the repo and a fresh clone has none.
- **Strategy data ages every season.** It was written for Y11S3 (Operation Split Fire). Y11S4 is expected early December 2026; see the season refresh steps in [KNOWLEDGE_DATA.md](KNOWLEDGE_DATA.md#season-refresh).
- **Windows only.** Capture, hotkeys and the fallback voice are Windows APIs.
- **No CI.** Tests are run by hand before merging.
- **`main` is protected on GitHub** by a ruleset: changes go through a pull request, and force pushes and deletion are blocked. The local hooks in `.githooks/` catch mistakes before the push; see [CONTRIBUTING.md](../CONTRIBUTING.md#how-the-rules-are-enforced).
- **Cost.** Claude calls cost real money every match; `coach.budget_per_match` (default $3) caps it and the dashboard header shows the running total. Switching `vision.model` to Haiku cuts screen reads about 40× at some accuracy cost.
- **Voice licences.** Piper and Kokoro voice data have their own licences; check them before distributing the app.
- **The owner's in-game name is in the code and tests** (operator-card tests, HUD cases, `tools/real_scenarios.yaml`, the card fixtures), because they come from real matches. **Other players' names are not:**
  - In test data they are placeholders (`Mate_A`, `Enemy_A`, …), and they are covered in the fixture screenshots.
  - The HUD tests against the owner's real captures map placeholders back through `data/calibration/names.json`, which stays local.
  - Keep it that way: never commit another player's name, in text or in a screenshot.

## Good next steps

**Start with [BACKLOG.md](BACKLOG.md).** The 2026-11-06 review of a real match found that the advice is too wordy, repeats itself and sometimes states stale facts. Those fixes are tracked as issues in the milestone [Review 2026-11-06: usable advice](https://github.com/Dmhost88/R6Coach/milestone/1), and they come before the items below.

The items below are roughly in order of value. Each is a branch of its own (see [CONTRIBUTING.md](../CONTRIBUTING.md)).

1. **CI**: a GitHub Actions workflow on `windows-latest` that installs requirements and runs `pytest -q`, then make it a required check on `main`.
2. **Y11S4 season refresh** (`data/season-y11s4`) when the season lands.
3. **Hand-check the live map** room boxes per map, starting with the most played.
4. **16:9 support**: capture frames at 1920×1080 and 2560×1440 with `--collect`, then fix regions in `hud.py`, `screens.py`, `opcards.py`.
5. **Profile pattern mining** (v3 remainder): recurring mistakes across matches, fed into prep advice.
6. **More real-match evals**: record a match per map with `--collect --quiet`, add scenarios to `tools/real_scenarios.yaml`.
7. **Packaging**: a one-file installer so a non-developer can run it.

## Local-only things you will not get from the repository

These live under `data/` or in git-ignored files on the owner's PC. A new developer creates their own:

- `.env` with API keys, `config.yaml` with personal settings (player name, operator pool, PTT key).
- `data/calibration/`: the real captured frames some tests use. Without them those tests skip.
- `data/frames/`, `data/evals/`: frames and eval results from real matches.
- `data/maps/`: Ubisoft blueprints (`tools/fetch_blueprints.py` re-downloads them).
- `data/models/`: voice models (downloaded on first use).

## Working with AI agents on this repo

The project was built with Claude Code. [CLAUDE.md](../CLAUDE.md) gives an agent the project rules, and [.claude/rules/feature-branches.md](../.claude/rules/feature-branches.md) is the branch workflow it must follow. If you change the workflow in CONTRIBUTING.md, change the rule too.
