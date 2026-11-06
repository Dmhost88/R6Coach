# Tabs

The dashboard's top-level navigation, a row of text tabs on `surface` with a `signal` underline on the active one.

- Markup: `<nav class="rc-tabs" role="tablist">` with `<button role="tab" aria-selected="true|false">` children. The consumer toggles `aria-selected`; the style follows it.
- Five tabs at most, sentence case, in the order a player needs them: Live first.
- Don't add counts or badges to tabs; status belongs in the header pills.
