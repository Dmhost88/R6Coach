# Button

Plain buttons for every action; **primary** (`signal` fill, `on-signal` text) at most once per view, for the thing the view is for: "Ask", "Save settings", "Save notes".

- Markup: `<button class="rc-btn">`, add `rc-btn--primary` or `rc-btn--quiet`. The consumer provides the label.
- Quiet is for header controls the player taps mid-match: Mute, Repeat, Skip, Pause reading. Keep them in that order.
- Labels start with a verb, sentence case, two or three words. A toggle swaps its label ("Mute" / "Unmute") instead of showing a checked state.
- Don't: icon-only buttons, red destructive styling for Skip, more than one primary in a card.
