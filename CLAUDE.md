# Field Desk — agent guide

## Product copy (voice)

The **default product voice** is **plain, clear, professional**. All
user-facing UI strings — labels, buttons, headings, empty states, loading
states, errors, tooltips, emails, toasts — are short, scannable, and honest.
No marketing fluff ("leverage", "seamless", "unlock", "empower", "supercharge",
"delightful"…), no exclamation spam. Plain voice is the default for every UI
string except the one place noted below.

**Grug voice is confined to ONE place**: the expandable "grug explains" section
per gem (the `GrugExplainer` component in `web/`). That section explains, in
grug cadence: what the app does, what problem it solves, and why grug thinks
it's working. The grug rules below apply to that section's copy and to its
toggle label — nowhere else.

Grug rules (apply only inside "grug explains"):
- Tiny words, short sentences. Drop articles/pronouns when natural.
- Lowercase by default. grug see, grug understand, grug click.
- The tool may speak as "grug"; the user is "you".
- Plain truth, no marketing fluff. No exclamation spam.
- Still usable: simple, not cryptic. If grug phrasing is confusing, make it
  clearer — not cuter.

Always:
- **Machine-facing text stays normal**: code, comments, variable/field names,
  API contracts, commit messages, and docs are professional, not grug.
- Keep product nouns intact: **Field Desk**, **GemScore**, **cohort**.

Examples (grug-explains section only):
- what it does → "app help you split bill with friend. snap photo, grug do math."
- problem → "before, friends argue who owe what. now no argue."
- why working → "ads run 40 day straight. spend going up. grug think real."

The canonical version of this rule lives in `.cursor/rules/grug-copy.mdc`.
