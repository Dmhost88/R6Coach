# Gameplay review: 4 ranked matches, 2026-11-07 evening

Branch `fix/coach-talk-less` with the debug log (`-v`). Maps: Outback, Consulate, Chalet, Bank. 20 rounds, 339 spoken lines. Player verdict: about 60% happy: "lots of lag in operator picks", "pre-round advice not helpful or easy to index in real time", "struggling to figure out where I was, what to do and who to pick", "the coach not knowing the situation as fast as me, or as information was discovered, like bomb location".

Six reviews ran on the log and the saved frames: pick/ban latency, pre-round line quality, fact freshness, player location, live moments (rerun of [2026-11-07-live-moments.md](2026-11-07-live-moments.md)), and a Fable design review. No API calls were made. This file merges them.

## The numbers

| What | Tonight |
|---|---|
| Op select: screen seen → first word | 7.2 s median, 10.3 s p90 |
| Op select: screen seen → first line finished | 12.8 s median, 16.5 s p90 |
| Attack: player locks after | about 5.5 s, so before the coach speaks |
| Op select lines | 128 queued, 86 played, 32 cut off, 11 played into prep |
| Pre-round lines heard per round | 9.4 (63 s of speech) |
| Pre-round lines correct / actionable / indexable | 66% / 47% / 61%; all three: 35% |
| Lines that guessed the site ("Site unknown, so…") | 24 |
| Defense rounds where the site was never known | 6 of 10 (2 read correctly, then discarded) |
| Enemy plants seen while defending | 1 of 3 |
| False "new round" resets | 8 |
| Cloud reads that mistook a hovered operator for a lock | 8 rounds |
| Room label read (prep / action) | 65% / 74% of HUD reads; 41% match a known callout |
| Live lines usable (action phase) | 47%, up from 20% on 11-06 |
| Live repeats | 6%, down from 22% |

Ops ban works when it is pre-planned at round end (16 of 20 played about 1 s after the screen appeared). The four round-1 bans took 8–10 s.

## Root causes, grouped

### 1. The coach's picture of the game is wrong (bugs, all local, no cost)

- **Bans carry over between rounds**: `watcher.py:527` and `:793-794` merge them, and `twin.py:43` says "Banned so far this match". This led to lines such as "Kaid is banned" when the player had locked Kaid.
- **False round resets**: prep → operator_select counts as a new round (`coach.py:271`, `watcher.py:459`/`:755`). It fires on the in-prep loadout menu, on loading frames read as prep, and on late cloud reads. It wipes the site, the lock and the teammates, and starts the pick advice again.
- **Discarded late reads drop the site**: `watcher.py:320-335` keeps only the bans and the round number.
- **A hovered operator is taken as the lock**: `llm.py:227-228` and `watcher.py:757-761`. Each one cut speech and started a new Claude call. 5 of the 9 pivots were operators the player never locked.
- **The defense site is never set**: the vote is stored as `site_options` only (`watcher.py:766-792`, `llm.py:177`), and padlocked sites are still offered (`twin.py:30`). The prompts then ask the model to guess (`coach.py:619-637`). As a result `_prep_cache` never fired once.
- **Banner parsing**:
  - `hud.py:180` needs `BOMBSFOUND` exactly, so "BOMBS FOUND 3M" fails.
  - `hud.py:170` has no `ENEMIES PLANTED THE DEFUSER`, `ACTIVATED DEFUSER FOUND` or `DEFUSER PICKED UP`.
  - "Enemies found the bombs" is never stored, and `tactical.py:130` drops it during prep.
- **Room labels**:
  - "1F" OCRs as "F" and the label is discarded (`hud.py:68`, `:293`).
  - OCR typos go straight into `my_location` (`watcher.py:624`).
  - "EXT" matches the room name on any floor (`maplayout.py:121`).
  - 28% of room changes were OCR noise only, and those set off check-ins.

### 2. Pre-round speech is LLM-first, long, and triggered by screens

- A pick line waits for the cloud to confirm the screen (2.7 s, `watcher.py:400-406`), then for the defense site (4.6 s, `coach.py:303-305`), then for Claude (1.9 s). It then takes about 8 s to say (21 words).
- There is no pre-planned pick (there is one for bans, `coach.py:213-231`).
- Prompts ask for "2-3" lines with a match-history line in every reply (`coach.py:28`, `:77-78`). The "say why" line adds filler (`:603`), and round 1 hedges both sides (`:592`).
- `operator_pool` doesn't match what the player plays (Lesion 5 rounds, plus Pulse, Sentry, Bandit). Lesion has no setup brief on any of the four maps.
- The Piper voice loads alongside the rest of startup (14.9 s), so the first ban line finished after the pick screen had appeared.

### 3. Live calls: much better, with leaks

- The 14-word cap keeps the leading clause and loses the instruction ("With 3 v 4."). It also runs before `clean_line`, so self-talk got through.
- Check-ins skip the fight settle and fire on OCR room noise.
- A kill during a call leaves a stale count ("Down 2 v 5").
- Debriefs get cut by the 24-word speaker cap, so the fix half is lost. They don't know the round's own calls, and the score lags one round.
- No low-time push on attack without a plant. A match win is never announced.

## Fix plan, ranked

| # | Branch | What | Why first | Cost |
|---|---|---|---|---|
| 1 | `fix/game-state-truth` | Per-round bans; no prep→select round reset (loadout overlay, late reads, loading); keep site/lock/teammates on discarded reads; hovered ≠ locked; defense site from the vote / READY tab / first prep room vs `site_options`; drop padlocked sites; banner parser by "contains" with enemy plant, defuser found/picked up, `objective_found_by_enemy`; 1F "F" fix, snap rooms to known labels with a two-read agreement, EXT = exterior | Every other fix speaks from this state. All local, removes cloud calls | none |
| 2 | `feature/instant-preround` | Deterministic lines from knowledge data, spoken from the cache in about 1 s: "Ban X." / "Pick X. Site A–B." / prep "Reinforce: …" "Gadget: … at …" "Hold: …", each ≤12 words, prep lines timed by the HUD timer. Pre-plan the pick during ban. Claude only refines, with a deadline (ban 8 s, pick 10 s, prep 20 s); late answers go to the dashboard. Never voice a site guess. Drop the 4 s wait; accept local op select without the cloud. Warm Piper before capture. Re-advise in one line when the site changes. Pool from the profile's real locks | Fixes the lag and indexing complaints directly | needs `tools/eval_real.py` before/after (about $1) |
| 3 | `data/hud-callouts-briefs` | Game HUD labels as names/aliases on the 4 maps (Party Hall, Back Alley, 2F Square, EXT Balcony, …); Lesion, Pulse, Sentry, Bandit briefs; Bank Alley Access spawn; a `bans:` block per map | #2 depends on this data | none |
| 4 | `fix/live-call-leaks` | `clean_line` before the cap, never keep a verbless clause; no check-in within KILL_SETTLE of a kill; drop a line if a kill landed after it was asked; fuzzy room compare; 1:00/0:30 pushes on attack with no plant; debriefs get the round's calls and the post-round score; announce a match win | Live usability 47% → target 65%+ | eval |
| 5 | `feature/dashboard-you-are-here` | Last-known "You" pin with its age, a large "You: 2F Square" banner, a floor badge, and the pre-round card (why, alternatives, loadout) that voice no longer says | Glanceable answer to "where am I" | none |

Later: top-bar portrait matching (enemy reveals, teammates in round), a "ROUND N" HUD read, and drone/camera view detection so a drone's room isn't taken for the player's.

All of this keeps fair play: menus and HUD only, no 3D-world enemy analysis, no input to the game. #1 and #2 remove cloud reads rather than adding them.
