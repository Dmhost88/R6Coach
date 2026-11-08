# R6 Coach: live-moment review of what the coach actually said (2026-11-06)

This is a read-only review of `data/profile.sqlite`, `data/coach.log` and the frame JSON in `data/frames/2026-11-06/`, checked against `main` at `d576aaa`. Other players' names are shown as `[player]`. The evidence and the analysis scripts are the owner's local data and are not in the repository. The fixes it led to are on `fix/coach-talk-less`; see [BACKLOG.md](../BACKLOG.md).

The 2026-11-06 Fable review covered the afternoon Chalet match (S3) and counted volume and repetition. This review adds the other two matches (S1, S2) and measures four things the Fable review did not: timing against the screen, speech time against each phase's clock, overlap with fights, and how many lines the player could have used.

## Headline numbers

| | S1 Oregon 02:13–02:36 | S2 Chalet 09:16–09:58 | S3 Chalet 12:54–13:34 | All |
|---|---|---|---|---|
| Rounds with lines | 5 | 9 | 8 | 22 |
| Lines | 46 | 187 | 136 | 369 |
| Lines per round | 9.2 | 20.8 | 17.0 | 16.8 |
| Words per line | 10.4 | 15.1 | 24.1 | 17.9 |
| Audio (estimated) | 3.2 min | 18.4 min (~45% of the match) | 20.8 min (~52%) | 42.5 min |
| Usable | 22 (48%) | 98 (52%) | 85 (62%) | 205 (56%) |
| Usable in the action phase | 0/15 | 12/48 | 10/47 | 22/110 (20%) |
| Live lines while alive that started right after a kill | 12/13 | 18/36 | 18/38 | 48 |
| Late | 1 | 30 | 3 | 34 |
| Debrief lines over the ban/pick screen | 0 | 10/15 | 0 (none landed) | 10 |
| Wrong or contradicted | 4 | 7 | 1 | 12 |
| Generic | 39% | 9% | 9% | 13% |
| Repeats (whole match / action phase) | 7% / 13% | 7% / 17% | 14% / 30% | 9% / 22% |
| Rounds with one instruction said 3+ times | 0 | 7/9 | 6/8 | 13/22 |

- **Speech rate:** about 0.36 s per word (Piper at speed 1.05), measured from back-to-back streamed lines, n=193.
- **Line length:** commit `17ebaea` (11:36, between S2 and S3) raised the caps from 16 to 24 words for coach lines and from 14 to 22 for live calls. S3 then had 27% fewer lines per round than S2 but 13% more audio.
- **Ban and pick** (about 45 s in total): S2 averaged about 48 s of speech per round and S3 about 67 s (49–83 s), with 4–8 pick lines a round.
- **Prep** (45 s): S1 talked for about 48% of it, S2 for 53% (median; R4 97%) and S3 for 55–75%.
- **Alive in the action phase:** S3 spoke about 45 s per round, with a median gap of 18 s between calls. S2 spoke about 22 s per round, with a median gap of 20 s.
- **Densest bursts:** every session's densest burst was on the pick screen. In S3, 7 lines (about 67 s of audio) started within 60 s.
- **Longest silences:** 85 s at match point in S3 R8, after the budget gate turned the Claude calls off, and 66–94 s in S1.
- **Data hygiene:**
  - The 3 matches were stored as 6 match ids, plus 49 lines with a NULL match id.
  - Round numbers drift.
  - `live.py:138` logs every fixed line as `action_phase`.
  - `profile.log_line` stores no screen, round, alive state or trigger.

## Rules used

**Timing** (the first rule that matches wins):
- **combat:** a kill-triggered or count-change line while the player is alive.
- **spectating:** an action-phase line after the player died.
- **late:**
  - a ban line after operator select is on screen
  - a pick line more than 10 s after the pick clock ended
  - a prep line in the last 8 s of prep
- **menus:** a debrief played while the ban or pick screen showed.
- **right:** everything else.

**Content:**
- **wrong:** contradicted by the HUD or game state (12 cases, checked by hand).
- **generic:** a fixed line, or a line naming no operator, room, gadget or number.
- **meta:** the coach talking about its inputs.
- **praise:** a death debrief that opens with praise.
- **specific:** everything else.

**Repeat:**
- **near:** a SequenceMatcher ratio of 0.6 or more, or a content-word Jaccard of 0.45 or more, against an earlier line in the same round.
- **chain:** one instruction said 3 or more times in a round.
- These rules are lexical, so they give a lower bound.

**Usable** = right timing, specific content and not a repeat.

## Top problems, causes and fixes

| # | Pattern | Code | Fix |
|---|---|---|---|
| 1 | Live calls are long, frequent and restate each other, and they fire into trades (S3: 45 s per round alive, 47% right after a kill, 30% repeats) | `tactical.py:32` (22-word cap in the prompt only), `:99-102` (check-in every 20 s), `:103-115` (trigger on every kill and count change), `:126` (gap measured from the trigger), `:138-142` (no history), `tts.py:487` (no cap or dedupe) | 14-word cap in code; pass the last 2 calls and add `changed: bool`; check in only on a room change; wait 5 s after a kill; measure the gap from the last line spoken; drop lines too similar to the last 60 s |
| 2 | Ban and pick talk runs past the clock, and pivot briefs stack | `coach.py:28` (2-4 lines), `:98` (24 words), `:304-309` (repick brief), `:366-380` (`_check_lock` full brief) | Speech budget per screen = seconds left × 2.7 words/s − 5 s; one-line pivots after two agreeing lock reads; drop loadout lines once loadout is over |
| 3 | Prep is half talk, and fixed lines cut into the plan | `coach.py:694-705` (no limit on steps), `live.py:37,163` (`found_bombs`), `live.py:158-160` (reinforce nag) | Plan of 2 lines, 14 words or fewer each; skip the reinforce nag if the plan said "reinforce" in the last 30 s; retire `found_bombs` |
| 4 | Debriefs play over menus or not at all; death debriefs open with praise | `coach.py:686` (deep, Opus at high effort), `:30` (3 lines), `:667` (praise in line 1), `:273` (cancels a slow debrief) | One line on the fast model at low effort; death debrief = one mistake-plus-fix line; no spectator calls while a death line is queued |
| 5 | Generic fixed lines repeat, and they are the fallback when Claude calls stop | `live.py:177,179` (`once=False`), `:35` ("Nice. Reposition.") | Once per round; drop `my_kill`; fill the fallback lines from the HUD (count, time, the player's room) |
| 6 | Lines play after the state they assumed has changed | `tts.py:567` (checks only ttl and mute) | Tag each queued line with its assumed operator, round, alive state and screen, and drop it at play time if any of them changed |

**Pick lines that contradicted the screen (S2):**
- "Go Twitch. Your team has Thermite…" was said to the player who was the Thermite.
- "Kaid is banned, so lock Mira" was followed 28 s later by "Swap to Kaid".

## Yesterday's Fable complaints in the current code

- **Still happen:**
  - Live calls have no memory of the last call.
  - Word budgets are in the prompt only.
  - Check-ins run on a 20 s timer.
  - "They know the site" is still spoken.
  - Debriefs run at deep effort.
  - The state is not checked when a line is played.
- **Partly fixed:** a death debrief still opens with praise, though softened.
- **Fixed:**
  - self-talk
  - ban side
  - stale counts and timer
  - site carry-over
  - announcing the budget gate
  - map recovery after a restart (in code; not verified on live data)

## Proposed live-moment score

**Script:** `tools/live_score.py --since <date> [--match <id>]`. It opens `profile.sqlite` read-only, makes no API calls, writes `data/evals/live_score-<date>.json`, and compares with the previous run.

**Prerequisite:** `coach_lines` gains these fields:
- `screen`
- `round_seq`
- `player_alive`, `alive_us`, `alive_them`, `timer_s`
- `secs_since_kill`
- `source`
- `trigger`
- `played_s` and `cut`

| Metric | Green | Red |
|---|---|---|
| orphans (NULL match id) | 0 | >0 |
| words_p90 | live ≤14, coach ≤18 | live >18, coach >24 |
| duty_pick (speech s / screen s) | ≤50% | >80% |
| duty_prep (speech s / 45) | ≤35% | >60% |
| duty_alive | ≤12% | >25% |
| combat_rate (alive lines < 5 s after a kill) | ≤10% | >30% |
| repeat_rate | ≤5% | >15% |
| chains (one instruction 3+ times) | ≤1 per match | ≥3 |
| generic_rate | ≤10% | >25% |
| late_rate | ≤5% | >15% |
| debrief_land | ≥80% | <40% |
| praise_open | 0% | >30% |

**Headline:** 100 × usable / lines. The script exits 1 if there are orphans or 2 or more metrics are red.

**Baseline:** 56 (S1 48, S2 52, S3 62). Red metrics:
- words_p90
- duty_pick
- duty_prep
- combat_rate
- chains
- debrief_land
- praise_open
- orphans

## Limits

- Combat is inferred from the line text and the trigger.
- Screen boundaries come from about 90 frames.
- Speech time is estimated.
- The list of wrong lines is not exhaustive.
- S1 and S2 ran code from before `17ebaea`. The causes were checked on current `main`.
