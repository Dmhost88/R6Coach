# StateList

The detected game state as label–value pairs: map, side, site, spawn, round, operators, bans, timer, alive, pinged.

- Markup: `<dl class="rc-state">` of `dt`/`dd` pairs. Add `rc-num` to a `dd` holding a live number (timer, alive, round) so it sits in `mono`; `rc-empty` for an unknown value shown as an en dash.
- Keep the row order stable so the eye learns where things are; never hide a row when it is empty.
- Values are what the screen showed, no guesses. Unknown is "–", not "?".
