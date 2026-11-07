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
| [#3](https://github.com/Dmhost88/R6Coach/issues/3) | Player counts and timer leak into the next round | watcher, hud |
| [#11](https://github.com/Dmhost88/R6Coach/issues/11) | Wrong state after a restart; invented bans | watcher |
| [#8](https://github.com/Dmhost88/R6Coach/issues/8) | Prep plan uses a stale site or lands too late | coach, watcher |
| [#5](https://github.com/Dmhost88/R6Coach/issues/5) | Model self-talk spoken; ban prompt doesn't say which side bans | coach, voice |
| [#9](https://github.com/Dmhost88/R6Coach/issues/9) | Push-to-talk answer wipes the voice queue | voice |
| [#10](https://github.com/Dmhost88/R6Coach/issues/10) | Budget limit goes silent without saying so | coach |

### Wave 2: say less, and on time (P1)

| Issue | What | Area |
|---|---|---|
| [#4](https://github.com/Dmhost88/R6Coach/issues/4) | Debriefs never land: fast model, one line | coach |
| [#6](https://github.com/Dmhost88/R6Coach/issues/6) | In-round calls repeat or reverse the last call | coach |
| [#7](https://github.com/Dmhost88/R6Coach/issues/7) | Enforce word budgets in code | coach |

#6 and #7 both touch `tactical.py`. Do them in sequence, or on one branch if one developer takes both.

### Wave 3: make the second monitor useful (P2)

| Issue | What | Area |
|---|---|---|
| [#13](https://github.com/Dmhost88/R6Coach/issues/13) | Room chips under the current line never show | dashboard |
| [#14](https://github.com/Dmhost88/R6Coach/issues/14) | Match spots and walls, not just rooms; ignore hedges | dashboard, knowledge |
| [#15](https://github.com/Dmhost88/R6Coach/issues/15) | Current instruction readable at a glance | dashboard |
| [#16](https://github.com/Dmhost88/R6Coach/issues/16) | Change-gate state updates; replay on reconnect | dashboard |
| [#12](https://github.com/Dmhost88/R6Coach/issues/12) | Death debrief: no praise opener; use the kill cam | coach, hud |
| [#17](https://github.com/Dmhost88/R6Coach/issues/17) | Clean room names before they reach the profile | knowledge |

#13 and #16 both change the event flow between `app.py`, the bus and `app.js`, so do #13 first.

### Wave 4: bigger bets (P3, weeks)

| Issue | What | Depends on |
|---|---|---|
| [#24](https://github.com/Dmhost88/R6Coach/issues/24) | Breach and utility spots drawn on screenshots and the map from one record | #14 (spots and walls in the layout) |
| [#18](https://github.com/Dmhost88/R6Coach/issues/18) | Plan of record per round; live calls as diffs | #6 |
| [#19](https://github.com/Dmhost88/R6Coach/issues/19) | Read the player's own gadget count from the HUD | none; enables setup steps ticking off |
| [#21](https://github.com/Dmhost88/R6Coach/issues/21) | Drop queued lines whose state moved on | #3 |
| [#20](https://github.com/Dmhost88/R6Coach/issues/20) | Shrink the cached prompt prefix | none; measure with the eval |
| [#22](https://github.com/Dmhost88/R6Coach/issues/22) | Live-call eval from real match data | none; do it before #18 to measure it |
| [#23](https://github.com/Dmhost88/R6Coach/issues/23) | Cross-match learning from takeaways | #17 |

## Picking up an issue

1. Assign yourself on GitHub so nobody else starts it.
2. Branch from an up-to-date `main`, named for the issue type: `fix/3-round-state-reset`, `feature/6-live-call-memory`.
3. Read the issue's **Evidence** and **Where**. File:line references are as of commit `b43c1f1` and may have moved; search for the function named.
4. Write the test first where the issue says "test". Most state bugs can be reproduced with `ScreenRead` sequences in `tests/test_watcher.py`, and coach behaviour with the fakes in `tests/test_coach.py`.
5. Open the PR with `Fixes #N` in the description, and fill in the template. Prompt, model or effort changes need `tools/eval_real.py` scores before and after; it costs about $1, so ask the owner first.
6. HUD, voice and timing changes can only really be checked in a match. Say in the PR what you tested, and ask the owner to play one game on the branch.

## Things to know

- **In flight:**
  - PR #1 (`feature/setup-briefs-live-map`) has the setup briefs, the live-map icons, the audio-crash fix and the round-counter fix.
  - PR #2 (`docs/handover-and-workflow`) has the handover docs.
  - This backlog is stacked on PR #2. Base new branches on `main` after those merge.
- **The review's evidence is local** to the owner's PC (`data/coach.log`, `data/frames/`, `data/profile.sqlite`), and is not in the repository. If you need a frame or log excerpt, ask the owner. Don't commit them: they contain other players' names.
- **Fair play is not negotiable.** The coach may use the HUD, the menus and the player's own pings only. Never use enemy positions from the 3D world, and never send input to the game. Every PR confirms this ([ARCHITECTURE.md](ARCHITECTURE.md#invariants-to-keep)).
- **Cost.** Every match costs real API money, capped at `coach.budget_per_match` (default $3). Moving a phase to the fast model (#4) or shrinking the prefix (#20) saves money. Adding a cloud call needs a gate.
- **Hooks.** Run `git config core.hooksPath .githooks` once per clone, so commits and pushes to `main` are refused.

## Definition of done for the milestone

A real Ranked match where:
- the coach speaks under 8 minutes in total;
- no in-round instruction is repeated within 60 s;
- no stale count or wrong side is spoken;
- the debrief is spoken in most rounds;
- every spot it names lights up on the map, with a picture for breach and utility spots.

The owner plays it and re-scores it against the review.
