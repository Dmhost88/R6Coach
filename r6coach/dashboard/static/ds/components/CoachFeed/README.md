# CoachFeed

The transcript of everything the coach said, newest first. The most-read thing in the app, set in `coach-line`.

- Markup: `<ul class="rc-feed">` of `<li>` rows, each a `.rc-feed__t` timestamp and a `.rc-feed__body`. Start a coach line with `<span class="rc-feed__phase">` naming the phase.
- Row states: `is-speaking` on the line playing now (`signal-soft` tint, amber phase tag; remove it when audio ends), `is-you` for your push-to-talk or typed questions (`team-us`, prefixed "You:"), `is-error` for errors (`bad`, prefixed with the source).
- Lines arrive at the top without sliding. Cap the list (200 rows) rather than paginating.
- Don't bold keywords inside a line or add icons per row; the sentence is the content.
