/**
 * Buffer + refill logic for the review deck. The deck holds a small window of
 * cards; after each rating a debounced background refill fetches a freshly
 * ranked batch (the server rescored with the rating just given) and replaces
 * the unseen tail. Pure module — the React component is a thin consumer.
 */

/** Cards fetched per request — the visible buffer stays about this deep. */
export const REFILL_LIMIT = 12;

/** Keep the current card and this many upcoming cards stable across a refill
 * so the deck never visibly shifts under the user. */
export const KEEP_AHEAD = 1;

/** How many of the most recent session-rated ids ride along in `exclude` —
 * guards the race where a refill lands before the rating row commits. Older
 * ratings are excluded server-side anyway. */
export const RECENT_RATED_SENT = 20;

type HasAdvertiserID = { advertiser_id: number };

/** Replaces the unseen tail of the deck with a freshly ranked batch, keeping
 * everything up to and including the next card after the current index. */
export function mergeRefill<T extends HasAdvertiserID>(
  cards: T[],
  idx: number,
  fresh: T[],
): T[] {
  const kept = cards.slice(0, idx + KEEP_AHEAD + 1);
  const keptIds = new Set(kept.map((c) => c.advertiser_id));
  return [...kept, ...fresh.filter((c) => !keptIds.has(c.advertiser_id))];
}

/** The ids a refill must not serve again: everything still ahead in the deck
 * (including the current card) plus the newest session ratings. */
export function refillExclude<T extends HasAdvertiserID>(
  cards: T[],
  idx: number,
  ratedIds: number[],
): number[] {
  const ids = new Set<number>();
  for (const c of cards.slice(idx)) ids.add(c.advertiser_id);
  for (const id of ratedIds.slice(-RECENT_RATED_SENT)) ids.add(id);
  return [...ids];
}

/** Debounced, single-flight runner: bursts of request() collapse into one run;
 * requests during a run queue exactly one catch-up run; errors are swallowed
 * (a refill is best-effort — the deck keeps serving its buffer). */
export class RefillScheduler {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private running = false;
  private again = false;
  private readonly run: () => Promise<void>;
  private readonly debounceMs: number;

  constructor(run: () => Promise<void>, debounceMs = 300) {
    this.run = run;
    this.debounceMs = debounceMs;
  }

  request(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.fire();
    }, this.debounceMs);
  }

  dispose(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }

  private async fire(): Promise<void> {
    if (this.running) {
      this.again = true;
      return;
    }
    this.running = true;
    try {
      await this.run();
    } catch {
      /* best-effort */
    }
    this.running = false;
    if (this.again) {
      this.again = false;
      void this.fire();
    }
  }
}
