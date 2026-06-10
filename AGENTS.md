# Field Desk — agent guide

## Grug-brained copy (product voice)

All **product-facing text** in this product (UI labels, buttons, headings, empty
states, errors, tooltips, emails, toasts — any user-visible string) is written in
**grug voice**: simple, blunt, caveman-cadence. grug see, grug understand, grug
click.

- Tiny words, short sentences. Drop articles/pronouns when natural.
- Lowercase by default; UPPERCASE only for big editorial display headings.
- The tool speaks as "grug"; the user is "you".
- Plain truth, no marketing fluff. No exclamation spam.
- Still usable: simple, not cryptic.
- **Machine-facing text stays normal**: code, comments, variable/field names,
  API contracts, commit messages, docs are professional, not grug.
- Keep product nouns intact: **Field Desk**, **GemScore**, **cohort**.

Full rule + examples: `.cursor/rules/grug-copy.mdc` (also mirrored in `CLAUDE.md`).
