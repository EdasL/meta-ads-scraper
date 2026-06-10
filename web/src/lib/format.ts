/**
 * format.ts — small pure formatting helpers (unit-tested, no DOM). `nowMs` is
 * passed in so they're deterministic; callers pass Date.now() at render time.
 */

/** Relative "time ago" from an ISO timestamp: "just now" / "5m ago" / "2d ago". */
export function timeAgo(iso: string, nowMs: number): string {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const s = Math.max(0, Math.round((nowMs - t) / 1000));
  if (s < 45) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.round(h / 24);
  if (d < 14) return `${d}d ago`;
  const w = Math.round(d / 7);
  if (w < 9) return `${w}wk ago`;
  const mo = Math.round(d / 30);
  if (mo < 18) return `${mo}mo ago`;
  return `${Math.round(d / 365)}y ago`;
}

/** Compact count for big numbers: 116000000 → "116M", 1530 → "1.5K", 980 → "980". */
export function compactCount(n: number): string {
  if (!Number.isFinite(n)) return "—";
  return new Intl.NumberFormat("en-US", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(n);
}

/** Relative label from a day-count (e.g. max_longevity_days → "31d ago"). */
export function daysAgoLabel(days: number): string {
  if (days <= 0) return "today";
  if (days < 14) return `${days}d ago`;
  if (days < 60) return `${Math.round(days / 7)}wk ago`;
  if (days < 540) return `${Math.round(days / 30)}mo ago`;
  return `${Math.round(days / 365)}y ago`;
}

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})/;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "M/D" from an ISO date string ("2026-04-09" → "4/9"). String-parsed, so a
 * date-only value never shifts a day across timezones. */
export function monthDayLabel(iso: string): string {
  const m = ISO_DATE.exec(iso);
  if (!m) return "—";
  return `${Number(m[2])}/${Number(m[3])}`;
}

/** "Mar 9, 2026" from an ISO date string. String-parsed (no timezone shift). */
export function isoDateLabel(iso: string): string {
  const m = ISO_DATE.exec(iso);
  if (!m) return "—";
  const month = MONTHS[Number(m[2]) - 1];
  if (!month) return "—";
  return `${month} ${Number(m[3])}, ${m[1]}`;
}

/** "Jul 10" from an ISO date string — month + day, no year. */
function monthDayName(iso: string): string {
  const m = ISO_DATE.exec(iso);
  if (!m) return "—";
  const month = MONTHS[Number(m[2]) - 1];
  if (!month) return "—";
  return `${month} ${Number(m[3])}`;
}

/**
 * The ad's run line, mirroring the Ad Library's "Started running on …":
 *   active: "Started Jan 15, 2026 · 146d"
 *   ended:  "Jul 10 – Aug 22, 2025 · 43d" (start year shown once when shared)
 * `ad_ended_on` is ignored while the ad is active (Meta sets end_date to the
 * last-seen day even for running ads). Returns null when the start is unknown.
 */
export function adRunLine(c: {
  ad_started_on?: string;
  ad_ended_on?: string;
  ad_active?: boolean;
  ad_days_running?: number;
}): string | null {
  if (!c.ad_started_on) return null;
  // 0 days = started and ended the same day — "<1d", never a confusing "0d".
  const days =
    c.ad_days_running != null ? ` · ${c.ad_days_running === 0 ? "<1" : c.ad_days_running}d` : "";
  if (c.ad_active || !c.ad_ended_on) {
    return `Started ${isoDateLabel(c.ad_started_on)}${days}`;
  }
  const sameYear = c.ad_started_on.slice(0, 4) === c.ad_ended_on.slice(0, 4);
  const start = sameYear ? monthDayName(c.ad_started_on) : isoDateLabel(c.ad_started_on);
  return `${start} – ${isoDateLabel(c.ad_ended_on)}${days}`;
}

/** Short labels for Meta publisher platforms ("FACEBOOK" → "fb"). */
const PLATFORM_SHORT: Record<string, string> = {
  facebook: "fb",
  instagram: "ig",
  audience_network: "audience",
  messenger: "messenger",
  threads: "threads",
};

/**
 * The per-creative meta line under an ad tile: "since 4/9 · 62d · EU reach
 * 1.2M · fb ig". Only renders what exists — US ads carry no impressions or
 * spend, so nothing is ever invented. Returns null when there is nothing to say.
 */
export function creativeMetaLine(c: {
  ad_started_on?: string;
  ad_days_running?: number;
  eu_total_reach?: number;
  publisher_platforms?: string[];
}): string | null {
  const parts: string[] = [];
  if (c.ad_started_on) {
    const since = `since ${monthDayLabel(c.ad_started_on)}`;
    parts.push(c.ad_days_running != null ? `${since} · ${c.ad_days_running}d` : since);
  } else if (c.ad_days_running != null) {
    parts.push(`${c.ad_days_running}d running`);
  }
  if (c.eu_total_reach != null) parts.push(`EU reach ${compactCount(c.eu_total_reach)}`);
  if (c.publisher_platforms && c.publisher_platforms.length > 0) {
    parts.push(
      c.publisher_platforms
        .map((p) => PLATFORM_SHORT[p.toLowerCase()] ?? p.toLowerCase())
        .join(" "),
    );
  }
  return parts.length > 0 ? parts.join(" · ") : null;
}
