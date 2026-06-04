# Field Desk — agent guide

## Grug-brained copy (product voice)

All **product-facing text** in this product — UI labels, buttons, headings,
empty states, errors, tooltips, emails, toasts, any user-visible string — is
written in **grug voice**: simple, blunt, caveman-cadence. grug see, grug
understand, grug click.

Rules:
- Tiny words, short sentences. Drop articles/pronouns when natural.
- Lowercase by default; UPPERCASE only for big editorial display headings.
- The tool may speak as "grug"; the user is "you".
- Plain truth, no marketing fluff ("leverage", "seamless", "unlock", "empower",
  "supercharge", "delightful"…). No exclamation spam.
- Still usable: simple, not cryptic. If grug phrasing is confusing, make it
  clearer — not cuter.
- **Machine-facing text stays normal**: code, comments, variable/field names,
  API contracts, commit messages, and docs are professional, not grug.
- Keep product nouns intact: **Field Desk**, **GemScore**, **cohort**.

Examples:
- "Discover validated opportunities" → "find idea that already work"
- "Loading your dashboard…" → "grug get board…"
- "No results match your filters." → "nothing here. loosen filter."
- "Authentication failed." → "that not right. try again."
- "Submit" → "go"  ·  "Reset layout" → "put back"  ·  "Active ads" → "ads now"

The canonical version of this rule lives in `.cursor/rules/grug-copy.mdc`.
