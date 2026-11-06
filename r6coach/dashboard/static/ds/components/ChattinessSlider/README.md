# ChattinessSlider

A labelled range row: per-phase chattiness (0 silent to 3 detailed) and volume.

- Markup: `.rc-field` holding `.rc-field__label`, `<input class="rc-range" type="range">` and `.rc-field__value` showing the current number in `mono`. The consumer updates the value on input.
- One row per phase, in match order: map ban, operator ban, operator select, prep, action, between rounds.
- The thumb and fill take `signal` via `accent-color`. Don't add tick labels; the number on the right is the label.
