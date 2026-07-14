/**
 * Pure, browser-and-node-safe helpers for the Hunt UI. No I/O, no imports beyond
 * the shared types — so `node --test src/lib/hunt.ts`'s sibling test can load it
 * with types stripped (the `import type` below is elided at runtime).
 *
 * `editTerms` mirrors the server's `normalizeHuntTerms`
 * (backend/controllers/hunt_controller.go): trim + lowercase, drop empty /
 * over-`MAX_TERM_LEN`, dedupe first-seen, cap at `MAX_TERMS`. Keeping the UI in
 * lockstep means the founder can't assemble a round the API would 422.
 */
import type { HuntProposal, HuntResult, HuntRound, HuntSettings } from "./api";

/** Mirror of the server bounds (maxHuntTerms / maxHuntTermLen). */
const MAX_TERMS = 15;
const MAX_TERM_LEN = 80;

/** Server defaults for the round-scrape knobs — a key equal to its default is
 *  omitted from `formatSettings` (it adds no information over the baseline). */
const DEFAULT_SETTINGS = { page_cap: 40, patience: 3, listing_pages: 10 } as const;

const norm = (s: string): string => s.trim().toLowerCase();
const isString = (v: unknown): v is string => typeof v === "string";

/** Extract the optional settings object from a raw proposal blob. Returns a
 *  fresh {page_cap?, patience?, listing_pages?} with only the numeric keys the
 *  server writes, or undefined when there is nothing to carry (empty / absent /
 *  malformed). Values are trusted as-is — the server has already clamped them. */
function parseSettings(raw: unknown): HuntSettings | undefined {
  if (raw === null || typeof raw !== "object") return undefined;
  const s = raw as Record<string, unknown>;
  const out: HuntSettings = {};
  if (typeof s.page_cap === "number") out.page_cap = s.page_cap;
  if (typeof s.patience === "number") out.patience = s.patience;
  if (typeof s.listing_pages === "number") out.listing_pages = s.listing_pages;
  return Object.keys(out).length > 0 ? out : undefined;
}

/**
 * Render a round's scrape settings as a compact, plain status fragment —
 * "depth 40 · patience 8 · listing 20" — showing ONLY the keys that differ from
 * the server defaults (page_cap 40 / patience 3 / listing_pages 10). Returns ""
 * when settings is undefined/empty or every key is at its default, so callers can
 * append it conditionally. Out-of-range values are shown verbatim (already clamped).
 */
export function formatSettings(settings: HuntSettings | undefined): string {
  if (!settings) return "";
  const parts: string[] = [];
  if (settings.page_cap != null && settings.page_cap !== DEFAULT_SETTINGS.page_cap) {
    parts.push(`depth ${settings.page_cap}`);
  }
  if (settings.patience != null && settings.patience !== DEFAULT_SETTINGS.patience) {
    parts.push(`patience ${settings.patience}`);
  }
  if (settings.listing_pages != null && settings.listing_pages !== DEFAULT_SETTINGS.listing_pages) {
    parts.push(`listing ${settings.listing_pages}`);
  }
  return parts.join(" · ");
}

/**
 * Validate a raw proposal blob into a {terms, competitor_apps} pair, or null if
 * it's malformed. The assistant message stores only these two arrays (the reply
 * is the message content); any extra keys — e.g. `reply` — are ignored, and
 * non-string array elements are dropped rather than trusted.
 */
export function parseProposal(raw: unknown): HuntProposal | null {
  if (raw === null || typeof raw !== "object") return null;
  const { terms, competitor_apps, settings } = raw as Record<string, unknown>;
  if (!Array.isArray(terms) || !Array.isArray(competitor_apps)) return null;
  const out: HuntProposal = {
    terms: terms.filter(isString),
    competitor_apps: competitor_apps.filter(isString),
  };
  // Only attach `settings` when the blob actually carries knobs — older messages
  // (and empty {}) leave it absent so the proposal round-trips as before.
  const parsed = parseSettings(settings);
  if (parsed) out.settings = parsed;
  return out;
}

/**
 * Apply a single add/remove to a term list under the server's normalization
 * rules, returning a fresh array. The incoming list is itself re-normalized
 * (trim/lowercase/dedupe, drop empty/over-length) so the result is always
 * submittable. An `add` that is empty, over length, a duplicate, or would push
 * past `MAX_TERMS` is silently refused (the list is returned unchanged for that
 * operation).
 */
export function editTerms(terms: string[], op: { add?: string; remove?: string }): string[] {
  const seen = new Set<string>();
  let out: string[] = [];
  for (const t of terms) {
    const n = norm(t);
    if (!n || [...n].length > MAX_TERM_LEN || seen.has(n)) continue;
    seen.add(n);
    out.push(n);
  }

  if (op.remove !== undefined) {
    const r = norm(op.remove);
    out = out.filter((t) => t !== r);
  }

  if (op.add !== undefined) {
    const a = norm(op.add);
    if (a && [...a].length <= MAX_TERM_LEN && !out.includes(a) && out.length < MAX_TERMS) {
      out.push(a);
    }
  }

  return out;
}

/**
 * Partition a round's results into on-target and off-target groups by Peon's
 * `relevance` judgement. Only an explicit "off_target" moves to `offTarget`;
 * "relevant" and null (unjudged — the round carried no intent) stay `onTarget`.
 * Order is preserved within each group so the caller renders rows in place.
 */
export function splitByRelevance(results: HuntResult[]): {
  onTarget: HuntResult[];
  offTarget: HuntResult[];
} {
  const onTarget: HuntResult[] = [];
  const offTarget: HuntResult[] = [];
  for (const r of results) (r.relevance === "off_target" ? offTarget : onTarget).push(r);
  return { onTarget, offTarget };
}

/** True while any round is still queued or running (drives live polling). */
export function huntIsActive(rounds: HuntRound[]): boolean {
  return rounds.some((r) => r.status === "queued" || r.status === "running");
}

/**
 * The round's progress events, newest-first, capped to `n`. Progress is written
 * append-only oldest→newest, so we reverse a copy. Returns [] when the round has
 * no events yet (e.g. `{}` progress).
 */
export function latestEvents(round: HuntRound, n = 50): { t: string; msg: string }[] {
  const events = round.progress?.events ?? [];
  return [...events].reverse().slice(0, n);
}
