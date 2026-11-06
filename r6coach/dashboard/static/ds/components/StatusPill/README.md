# StatusPill

A compact `name: value` readout for the header: capture, detected screen, push-to-talk, API cost.

- Markup: `<span class="rc-pill">`, with `rc-pill--live` (healthy, `good`), `rc-pill--warn` (needs attention or listening, `signal`), `rc-pill--error` (`bad`) or `rc-pill--plain` (no dot, for plain numbers like cost).
- The dot is decoration; the words carry the state. Write values in lowercase: "capture: stopped", never just a red dot.
- Push-to-talk switches to `--warn` while the key is held and back to `--live` on release. No pulsing.
