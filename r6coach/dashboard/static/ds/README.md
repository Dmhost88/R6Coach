R6 Coach is a calm analyst in your headset. It reads your own screen, knows the maps, and speaks only when it has something worth saying. The brand follows the product: quiet, exact, never in the way of the game. The dashboard lives on a second monitor and should be easy to ignore until you look at it.

## Principles

- **Calm over hype.** No neon, no glow, no glitch effects, no "GG EZ" energy. A broadcast analyst's desk, not a gaming peripheral box.
- **Fair play is visible.** Nothing may look like an overlay, radar or wallhack. No minimaps with enemy dots, no crosshairs or reticles, no red flashes meaning "enemy here". Show only what the HUD and menus already showed you.
- **The voice is the product.** The coach's lines are the most important text on screen. Everything else steps back.
- **Numbers are instruments.** Timer, score, alive counts and cost sit in `mono` with tabular figures so they don't jitter when they change.

## Voice

The coach is a pro analyst in your ear: concise, confident, unhurried. It talks to you ("you"), about your team ("we", "your team") and the enemy ("they").

Spoken lines (written for text-to-speech):
- Lead with the fact, then the action. "3 v 2. They have to come to you."
- One idea per line, under 12 words. Two short sentences beat one long one.
- Numbers as digits so TTS reads them cleanly: "45 seconds", "2 left".
- Name places the way the game's callouts do: "Basement CCTV", "Lockers", "Main stairs".
- No exclamation marks, no filler ("Okay so", "Alright"), no praise inflation ("Insane play").
- Never a reaction call ("enemy left", "he's peeking"). The coach comments on state, not on things you haven't seen.

Examples:
- Prep: "Defence on Basement. Reinforce the CCTV wall and the hatch above."
- Action: "Defuser down on Lockers. Play the plant, don't chase."
- Clock: "20 seconds. Commit or reset."
- Between rounds: "Lost the 1 v 1 on Main stairs again. Hold the long angle next round."

Written UI:
- Sentence case everywhere: buttons, tabs, card titles. "Read screen now", "Map notes".
- Buttons start with a verb. Status pills are `name: value` in lowercase: "capture: capturing".
- No emoji. Use the en dash for scores ("4 – 3") and "v" for player counts ("3 v 2").

## Colour

Dark is the primary theme; light exists for daytime and reports. Tokens are semantic: use the name, never the hex.

- Page on `ground`, cards on `surface`, hover and selected rows on `surface-raised`. Separate them with `line` hairlines, not shadows.
- Text in `ink`; labels, hints, timestamps and placeholders in `ink-muted`. `ink-faint` is for disabled controls only.
- `signal` amber is the coach. Use it for the primary action (once per view), the active tab, live indicators and the coach's own label. On a `signal` fill, text is `on-signal`, never white. Amber as text is `signal-ink`.
- `team-us` blue and `team-them` orange mean the two sides and nothing else: scores, alive counts, your own questions in the feed. Never use `team-them` for errors or warnings.
- `good` and `bad` are status. Always pair them with a word ("Win", "Loss", "capture: stopped"); colour is never the only signal.
- Inputs and selects are `ground` inside a `control-border` edge. Keyboard focus is a solid 2px `focus-ring` with a 2px offset on every focusable element.

## Type

Three open-source families (SIL OFL), bundled in `fonts/` so the dashboard works offline. Ship the files with the app; licences are in `licenses/`.

- `display` is **Hubot Sans**, GitHub's mechanical, variable sans. It has a width axis (75–125), so one family gives the narrow broadcast cut for titles and a normal width for labels. Set the width with `font-variation-settings: "wdth" N`: 85 for `display`, 90 for `heading`, 100 for `label`, 80 for the wordmark and the cover. Use `.rc-display` and `.rc-heading` from `bundle.css` rather than repeating the settings.
- `sans` is **Mona Sans**, Hubot's softer sibling, for everything you read: `coach-line` in the feed, `body` elsewhere, `body-sm` for hints. Keep it at width 100.
- `mono` is **IBM Plex Mono** for live numbers: `score`, `readout`, `stamp`. Add `font-variant-numeric: tabular-nums` wherever numbers update.
- Card titles use `label` in `ink-muted`, sentence case. Only the wordmark is set in capitals.
- Never use Graphik or any other commercially licensed face here.

## Space, shape, depth

- 4px grid: `space-2` inside controls, `space-4` card padding and gaps, `space-6` between sections.
- `radius-md` (6px) for buttons and inputs, `radius-lg` (8px) for cards, `radius-full` for pills. Nothing is fully square, nothing is bubbly.
- Flat. `shadow-float` only for menus that open over content.
- The dashboard is a two-column grid: a 280–380px state column and a fluid feed column, stacking to one column below 800px.

## Motion

Almost none. The dashboard is in your peripheral vision while you play, and movement pulls the eye.
- State changes cut, or fade in 120ms ease-out at most.
- New coach lines appear at the top of the feed without sliding; the newest line gets a `signal-soft` tint that fades over 4 seconds.
- Never blink, pulse or flash. The push-to-talk pill turns `signal` while listening and turns back when done.

## Iconography

The house set lives in `assets/Icons`: 24px grid, 1.75px rounded strokes, a grey line drawing with exactly one amber accent (the part that answers "what's happening": the ban slash, the reinforcement struts, the stopwatch hand).
- One icon per coach phase: `phase-map-ban`, `phase-operator-ban`, `phase-operator-select`, `phase-prep`, `phase-action`, `phase-recap`. Use them in front of the phase tag in the feed and on report section headings.
- Utility icons: `coach`, `push-to-talk`, `screen-read`, `drone-route`, `objective`, `round-win`.
- Show them at 16, 20 or 24px. Pair each with a word; never an icon-only control.
- When you inline them in the app, swap the grey stroke for `currentColor` (set to `ink-muted`) and the amber for `var(--signal)`.
- For anything the set doesn't cover, draw it the same way or fall back to Lucide at 1.75px.
- No operator portraits, game logos, gadget art or Ubisoft artwork anywhere.

## Illustration

Flat, geometric line drawings in the token palette, on a transparent ground so they work in both themes (`assets/Illustrations`). The recurring motifs are voice bars (the coach), a dotted planning grid (the analyst's whiteboard) and dashed routes (drones). Plans are always invented layouts, never a copy of a real Siege map, and never show enemy positions.
- `empty-waiting`: empty states, "Waiting for a match".
- `listening`: the push-to-talk overlay in the dashboard while recording.
- `splash-plan`: the tray app's about/launch screen and the README hero.
- `report-timeline`: the header of a post-match report. Each bar is a round (blue won, orange lost, amber the key round); regenerate it from real round data.
- Backgrounds: tile `assets/Patterns/pattern-plus.svg` behind empty areas at low emphasis; `pattern-bars.svg` as a divider strip. Never behind body text.

## Logo

The mark is four voice bars (`assets/Logos`). The lockup is the mark plus "R6 COACH" in Hubot Sans Bold at width 85, outlined to paths: `rc-lockup` on dark grounds, `rc-lockup-ink` on light. Leave clear space of one bar-height around it. In running text the name is "R6 Coach". "R6" and "Rainbow Six" are Ubisoft trademarks: fine for a personal tool, but rename before distributing.

## Components

The components are plain CSS classes in `components/bundle.css`, prefixed `rc-`, written for the dashboard's vanilla HTML. Load `tokens.css`, then `bundle.css`, then use the markup shown in each component's preview. Set `data-theme="dark"` or `data-theme="light"` on `<html>`.
