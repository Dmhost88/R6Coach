# Backlog: making the advice usable

This is the handoff for the work found in the 2026-11-06 review. It covers what to fix, in what order, and how to pick up an issue. Read [HANDOVER.md](HANDOVER.md) first for the project as a whole.

- **All issues:** milestone [Review 2026-11-06: usable advice](https://github.com/Dmhost88/R6Coach/milestone/1). The label `review-2026-11-06` covers the same set.
- **The review itself:** [reviews/2026-11-06-fable-review.md](reviews/2026-11-06-fable-review.md).

## The problem in one paragraph

The review scored the coach's advice 4/10 in a real 40-minute Ranked match on Chalet.

**What works:**
- The knowledge is good, and ban and pick advice is well grounded.
- Push-to-talk answers were the best lines of the day.

**What fails:**
- **Volume and repetition:** about 18–21 minutes of speech, and 31 of 47 in-round calls repeated or reversed the previous one.
- **False state stated as fact:** for example, player counts left over from the last round.
- **Debriefs never spoken:** they are too slow for the ~10 s gap after a round.
- **The model's own reasoning reaches the speaker.**

The owner muted it mid-match.

**The goal:** one true, short sentence per decision, silence when nothing changed, and a picture on the second monitor for anything about where to go.

## Order of work

Work through the waves in order. Within a wave, issues are independent unless noted. Each issue is one branch and one PR (see [CONTRIBUTING.md](../CONTRIBUTING.md)).

### Wave 1: stop saying wrong things (P1, days)

| Issue | What | Area |
|---|---|---|
| [#1](https://github.com/Dmhost88/R6Coach/issues/1) | Player counts and timer leak into the next round | watcher, hud |
| [#9](https://github.com/Dmhost88/R6Coach/issues/9) | Wrong state after a restart; invented bans | watcher |
| [#6](https://github.com/Dmhost88/R6Coach/issues/6) | Prep plan uses a stale site or lands too late | coach, watcher |
| [#3](https://github.com/Dmhost88/R6Coach/issues/3) | Model self-talk spoken; ban prompt doesn't say which side bans | coach, voice |
| [#7](https://github.com/Dmhost88/R6Coach/issues/7) | Push-to-talk answer wipes the voice queue | voice |
| [#8](https://github.com/Dmhost88/R6Coach/issues/8) | Budget limit goes silent without saying so | coach |

### Wave 2: say less, and on time (P1)

| Issue | What | Area |
|---|---|---|
| [#2](https://github.com/Dmhost88/R6Coach/issues/2) | Debriefs never land: fast model, one line | coach |
| [#4](https://github.com/Dmhost88/R6Coach/issues/4) | In-round calls repeat or reverse the last call | coach |
| [#5](https://github.com/Dmhost88/R6Coach/issues/5) | Enforce word budgets in code | coach |

#4 and #5 both touch `tactical.py`. Do them in sequence, or on one branch if one developer takes both.

**2026-11-07:** #2, #4 and #5, plus #10's praise opener, are done in code on `fix/coach-talk-less`, from the [live-moment review](reviews/2026-11-07-live-moments.md) of all three recorded matches. Their "done when" boxes need a real match on the branch and `tools/eval_real.py` scores before they close. Still open from that review: talk on the pick screen runs past the pick clock and pivots stack a second brief (no issue yet), and #19 (drop queued lines whose state moved on).

### Wave 3: make the second monitor useful (P2)

| Issue | What | Area |
|---|---|---|
| [#11](https://github.com/Dmhost88/R6Coach/issues/11) | Room chips under the current line never show | dashboard |
| [#12](https://github.com/Dmhost88/R6Coach/issues/12) | Match spots and walls, not just rooms; ignore hedges | dashboard, knowledge |
| [#13](https://github.com/Dmhost88/R6Coach/issues/13) | Current instruction readable at a glance | dashboard |
| [#14](https://github.com/Dmhost88/R6Coach/issues/14) | Change-gate state updates; replay on reconnect | dashboard |
| [#10](https://github.com/Dmhost88/R6Coach/issues/10) | Death debrief: no praise opener; use the kill cam | coach, hud |
| [#15](https://github.com/Dmhost88/R6Coach/issues/15) | Clean room names before they reach the profile | knowledge |

#11 and #14 both change the event flow between `app.py`, the bus and `app.js`, so do #11 first.

### Wave 4: bigger bets (P3, weeks)

| Issue | What | Depends on |
|---|---|---|
| [#22](https://github.com/Dmhost88/R6Coach/issues/22) | Breach and utility spots drawn on screenshots and the map from one record | #12 (spots and walls in the layout) |
| [#16](https://github.com/Dmhost88/R6Coach/issues/16) | Plan of record per round; live calls as diffs | #4 |
| [#17](https://github.com/Dmhost88/R6Coach/issues/17) | Read the player's own gadget count from the HUD | none; enables setup steps ticking off |
| [#19](https://github.com/Dmhost88/R6Coach/issues/19) | Drop queued lines whose state moved on | #1 |
| [#18](https://github.com/Dmhost88/R6Coach/issues/18) | Shrink the cached prompt prefix | none; measure with the eval |
| [#20](https://github.com/Dmhost88/R6Coach/issues/20) | Live-call eval from real match data | none; do it before #16 to measure it |
| [#21](https://github.com/Dmhost88/R6Coach/issues/21) | Cross-match learning from takeaways | #15 |

## Picking up an issue

1. Assign yourself on GitHub so nobody else starts it.
2. Branch from an up-to-date `main`, named for the issue type: `fix/3-round-state-reset`, `feature/6-live-call-memory`.
3. Read the issue's **Evidence** and **Where**. File:line references are as of commit `ad7ac51` and may have moved; search for the function named.
4. Write the test first where the issue says "test". Most state bugs can be reproduced with `ScreenRead` sequences in `tests/test_watcher.py`, and coach behaviour with the fakes in `tests/test_coach.py`.
5. Open the PR with `Fixes #N` in the description, and fill in the template. Prompt, model or effort changes need `tools/eval_real.py` scores before and after; it costs about $1, so ask the owner first.
6. HUD, voice and timing changes can only really be checked in a match. Say in the PR what you tested, and ask the owner to play one game on the branch.

## Things to know

- **Already on `main`:** the setup briefs, the live-map icons, the audio-crash fix and the round-counter fix that came out of the same match. Branch from `main`.
- **The review's evidence is local** to the owner's PC (`data/coach.log`, `data/frames/`, `data/profile.sqlite`), and is not in the repository. If you need a frame or log excerpt, ask the owner. Don't commit them: they contain other players' names.
- **Fair play is not negotiable.** The coach may use the HUD, the menus and the player's own pings only. Never use enemy positions from the 3D world, and never send input to the game. Every PR confirms this ([ARCHITECTURE.md](ARCHITECTURE.md#invariants-to-keep)).
- **Cost.** Every match costs real API money, capped at `coach.budget_per_match` (default $3). Moving a phase to the fast model (#2) or shrinking the prefix (#18) saves money. Adding a cloud call needs a gate.
- **Hooks.** Run `git config core.hooksPath .githooks` once per clone, so commits and pushes to `main` are refused.

## Definition of done for the milestone

A real Ranked match where:
- the coach speaks under 8 minutes in total;
- no in-round instruction is repeated within 60 s;
- no stale count or wrong side is spoken;
- the debrief is spoken in most rounds;
- every spot it names lights up on the map, with a picture for breach and utility spots.

The owner plays it and re-scores it against the review.
