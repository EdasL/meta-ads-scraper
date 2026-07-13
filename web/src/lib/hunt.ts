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
import type { HuntProposal, HuntRound } from "./api";

/** Mirror of the server bounds (maxHuntTerms / maxHuntTermLen). */
const MAX_TERMS = 15;
const MAX_TERM_LEN = 80;

const norm = (s: string): string => s.trim().toLowerCase();
const isString = (v: unknown): v is string => typeof v === "string";

/**
 * Validate a raw proposal blob into a {terms, competitor_apps} pair, or null if
 * it's malformed. The assistant message stores only these two arrays (the reply
 * is the message content); any extra keys — e.g. `reply` — are ignored, and
 * non-string array elements are dropped rather than trusted.
 */
export function parseProposal(raw: unknown): HuntProposal | null {
  if (raw === null || typeof raw !== "object") return null;
  const { terms, competitor_apps } = raw as Record<string, unknown>;
  if (!Array.isArray(terms) || !Array.isArray(competitor_apps)) return null;
  return {
    terms: terms.filter(isString),
    competitor_apps: competitor_apps.filter(isString),
  };
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
