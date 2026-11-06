# MatchTable

Recent matches and report tables: hairline rows, `label` headers, hover on `surface-raised`.

- Markup: `<table class="rc-table">` with `thead`/`tbody`. `rc-num` on number cells; `rc-win` and `rc-loss` on the result cell, which always holds the word.
- Columns read left to right as when, where, what happened: When, Map, Score, Result.
- An empty table shows one full-width row in `body-sm` muted: "No matches recorded yet."
