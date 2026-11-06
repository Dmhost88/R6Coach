# AskBar

The typed fallback for push-to-talk, under the coach feed: a full-width input and the view's one primary button.

- Markup: `<form class="rc-ask">` with `<input class="rc-input">` and `<button class="rc-btn rc-btn--primary">Ask</button>`. `rc-input` and `rc-select` also style every settings field and the notes textarea.
- The placeholder names both ways to ask. Clear the field on submit; the question then appears in the feed as an `is-you` row.
- Inputs fill with `ground` and edge with `control-border`, so they read as wells inside a card.
