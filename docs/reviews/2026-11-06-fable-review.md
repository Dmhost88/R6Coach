# R6 Coach review (Fable lead + 5 reviewers), 2026-11-06

Reviewers: lead, player, product designer, UI designer, web developer, latency/accuracy engineer. This is their report as returned.

Every gap below is tracked as a GitHub issue in the milestone [Review 2026-11-06: usable advice](https://github.com/Dmhost88/R6Coach/milestone/1). [BACKLOG.md](../BACKLOG.md) maps each gap to its issue and sets the order of work.

Since the review, the round-counter drift and the audio crash it mentions are fixed on `main`.

Other players' names from the match are replaced with placeholders in the repository.

The evidence (log, frames, profile database, the spoken-line dump) is the owner's local data under `data/` and is not in the repository. Times in the report are local times on 2026-11-06, from `data/coach.log`.

Evidence:
- `data/coach.log`
- every line spoken in the Chalet match (12:54–13:34)
- `data/frames/2026-11-06/`
- `data/profile.sqlite`
- the source
- live-API demos

Scripts and reviewer notes are in the session scratchpad under `fable-review/`.

## Verdict: 4 / 10

**What works:**
- The knowledge is good.
- Bans and picks are grounded in what the enemy ran.
- The push-to-talk answers were the best lines of the day.
- The local HUD path gives 2–3 s live calls at no cost.

**What doesn't:**
- The coach spoke 138 lines (3,283 words, roughly 18–21 minutes of audio) in a 40-minute match.
- 31 of 47 live calls restated or reversed the previous one.
- 0 of 5 debriefs were spoken.
- It stated false facts at key moments.
- The player muted it at 13:14:33, as a death debrief opened with praise.

"A very knowledgeable coach you want to turn off during the round."

## Top gaps, ranked

1. **Volume and repetition.**
   - Live calls average 23.6 words and come every ~20 s.
   - Each is written with no memory of the last one (`tactical.py:138–144`).
   - Mira was told "mirror Office–Library" seven times in 150 s, and Rook "drop armor" four times in 60 s.
   - Lesion got five different Gu-mine spots in 70 s.
   - A demo showed that prompt changes alone still produced a rephrase, so the fix has to be in code.
2. **False state spoken as fact.**
   - `_enter_screen` doesn't reset `alive_us/alive_them/timer_seconds`, so overtime R7 opened with "5 v 1", leaked from R6.
   - After a restart the map was None, and the coach called Chalet rooms Clubhouse.
   - A single OCR VS read rewrites the player counts.
   - A predicted side beat a read one.
   - The cloud invented an "Ace" ban.
3. **Debriefs never land.**
   - Opus at effort high can't produce one line in the ~10 s round-end gap, and every cancelled call still pays for thinking.
   - Sonnet at effort low did it in 3.4 s in a demo.
4. **Model self-talk is spoken aloud.**
   - Examples: "The screen shows round 3… but the match state says round 1"; "Ban Jager… so ban Nomad? No. Ban Hibana".
   - The ban prompt doesn't say which side the player bans.
   - The "bans last the whole match" rule contradicts the screen.
   - Nothing filters lines like these before TTS.
5. **Prep plans late or for the wrong site.**
   - The site carried over from R2 into R3.
   - In R5 the pivot cancelled the plan, and the replacement arrived with ~5 s of prep left.
6. **The kill cam is ignored.** "KILLED BY …" is in 30-pt text on screen, yet the log says the killer was unknown. Death line 1 is mandated praise.
7. **No read of the player's own gadget count** (bottom-right HUD). That count would stop the nagging and tick off prep steps.
8. **The budget gate goes silent with no log line.** At match point R8 the coach only gave canned lines. The 52.8k-token cached prefix costs $0.13–0.27 to rewrite after every restart, about $1.2 today.
9. **Memory and profile noise.**
   - Memory dies on restart.
   - `matches.takeaways` is written but never read.
   - Room names are misspelled in the database.
10. **Dashboard bugs and legibility.**
    - Room chips never show: the `_map_focus` subscription order means `nowSaid` clears what `mapFocus` filled.
    - The matcher misses "Half Wall", "basement" and "Blue stairs", and hedged phrases still zoom the map.
    - The current line is 23 px, shrinking to 17 px; it needs 40 px or more.
    - The glance view lacks `match_end`.
    - `state.updated` rebuilds the DOM every second.

## Quick wins (days)

**State:**
- Reset `alive_*`, `timer_seconds` and `defuser_planted` each round.
- Require two agreeing VS reads before changing the counts.
- Never let a predicted side beat a read one.
- Recover the map from callouts when it is None.

**Debriefs and lines:**
- Run debriefs and death lines on the fast model at effort low, one line each.
- Enforce word budgets in code (live 14 words, pick 2 lines, prep 3).
- Filter self-talk before TTS.
- Tell the ban prompt which side the player bans, and fix the bans rule.

**Live calls:**
- Pass the last two lines into each call.
- Add a `changed: bool` field and drop calls that don't change anything.
- Check in only when the player's room changes.
- Retire "They know the site".

**Prep and voice queue:**
- Clear `site` at round reset.
- Let the prep plan outrank leftover pick lines.
- Make a push-to-talk answer clear only the question phase.

**Reliability:**
- Announce "budget tight" once.
- Rebuild memory from `profile.sqlite` on start.
- Canonicalise room names before they reach the database.

**Dashboard:**
- Send `rooms` on `coach.said`, one event instead of two.
- Add aliases and spots to the layout YAML.
- Add a hedge filter.
- Show the current line at 40 px, never shrinking.
- Add `match_end` to the glance view.
- Change-gate `state.updated`.
- Add event sequence numbers with replay on reconnect.

## Bigger bets (weeks)

- **Plan of record per round:** the job is written once at lock. Live calls become diffs against it, picked from pre-written branches locally in under 100 ms.
- **HUD reads:** the player's own gadget count, plus kill-cam OCR.
- **Smaller cached prompt:** a per-site dossier and only the ten operators on screen (~6k tokens).
- **Check at play time:** drop a queued line if the state it was written for has changed.
- **Live-call eval** built from today's 47 calls.
- **Cross-match learning:** feed takeaways and death patterns into the next brief on the same map.

## Magic

- One true sentence per decision, and quiet when you're already doing it.
- The picture of a spot appears on the second monitor before the sentence ends.
- Prep steps tick themselves off as gadget counts drop.
- Your death shows your last frame, the killer's card and the spot to hold next time.
- The second match on a map is coached differently, using what happened in the first.

All of it uses only the HUD, menus and your own pings.

## Breach/utility screenshots with the map

One record, two spaces.

**Data:**
- `layout/<map>.yaml` gets per-floor `walls:` (two endpoints and the two rooms) and `spots:` (a point plus aliases).
- Setup placements anchor to a `wall:`, `spot:` or `room:`.
- Screenshot marks gain `kind: breach|utility|stand|look`.

**Drawing:**
- One SVG layer stack per floor: dim, sites, route, walls drawn as hatched bars, utility symbols, then the player ring.
- The screenshot uses the same marks and step numbers.

**Where and when pictures show:**
- They sit inside the map stage, at 60/40 during prep.
- They're auto-shown for 10 s when a spoken line names a placement, and can be called up by push-to-talk.

**Timing:**
- The defense brief comes at lock.
- The attack overlay comes at BOMBS FOUND.
- The death card shows during the kill cam.
- Never during the action phase.

## Reviewers' top three

**Player:**
1. Speak only on a change, in under 14 words.
2. The errors come from state, not knowledge.
3. Make the default as quiet and specific as the push-to-talk answers, with pictures for anything spatial.

**Product designer:**
1. Halve the words and triggers.
2. Read the player's own gadget count and keep a plan of record.
3. Make prep brief-driven with pictures, and feed takeaways back.

**UI designer:**
1. Show the instruction at 40–60 px and never shrink it.
2. Use WHERE / DO / WATCH slots that replace rather than stack.
3. Draw walls as walls, with the step picture in the map stage.

**Web developer:**
1. Make the map and the line one event.
2. Add spot and wall aliases, and have the coach output structured rooms.
3. Give marks a kind and a wall anchor.

**Latency/accuracy engineer:**
1. Fix stale state first.
2. Give live calls memory and a reason to stay silent.
3. Effort high with a 10 s window can't work.

## API spend

About $1.44 recorded; realistically $1.50–1.75. That is under the $5 cap.
