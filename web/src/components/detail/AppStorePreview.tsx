"use client";

import { useState } from "react";
import { compactCount, isoDateLabel, monthDayLabel, timeAgo } from "@/lib/format";
import { SketchLineChart, type ChartPoint } from "@/components/sketch";
import type { AppInfo, AppMetricPoint, AppReview, GemDossier } from "@/types/contracts";

/**
 * "Store listing" — the deep-dive on the gem's app listing: description,
 * screenshots, the app-side growth chart, and recent reviews. The identity
 * row (icon, name, rating, store link) lives in GemHeader at the top of the
 * page. Everything here is conditional on what the store actually returned;
 * Apple publishes no download counts, so the iOS trend is the rating-count
 * delta, labeled as a proxy.
 */

/** Five-glyph star rating (filled = rounded), with the numeric value beside it. */
function Stars({ rating }: { rating: number }) {
  const filled = Math.max(0, Math.min(5, Math.round(rating)));
  return (
    <span aria-label={`${rating.toFixed(1)} out of 5 stars`} style={{ letterSpacing: "0.06em" }}>
      <span aria-hidden>{"★".repeat(filled)}</span>
      <span aria-hidden style={{ color: "var(--faint)" }}>{"☆".repeat(5 - filled)}</span>
    </span>
  );
}

/**
 * Three-way store-presence chips for the detail header: iOS app / Android app,
 * or Web only when the gem has no linked store listing.
 */
export function StorePresenceChips({ apps }: { apps: AppInfo[] }) {
  const labels: string[] = [];
  if (apps.some((a) => a.store === "ios")) labels.push("iOS app");
  if (apps.some((a) => a.store === "android")) labels.push("Android app");
  if (labels.length === 0) labels.push("Web only");
  return (
    <span style={{ display: "inline-flex", gap: 6 }}>
      {labels.map((l) => (
        <span
          key={l}
          className="font-mono"
          style={{
            fontSize: "var(--fs-xs)",
            padding: "1px 8px",
            border: "1px solid var(--ink)",
            color: "var(--ink-soft)",
            whiteSpace: "nowrap",
          }}
        >
          {l}
        </span>
      ))}
    </span>
  );
}

/** One screenshot in the strip; hides itself if the image fails to load. */
function Shot({ src, index }: { src: string; index: number }) {
  const [broken, setBroken] = useState(false);
  if (broken) return null;
  return (
    // eslint-disable-next-line @next/next/no-img-element -- external store CDN images; next/image is a poor fit for an internal tool
    <img
      src={src}
      alt={`App screenshot ${index + 1}`}
      loading="lazy"
      onError={() => setBroken(true)}
      style={{ height: 230, width: "auto", flex: "0 0 auto", border: "1.5px solid var(--ink)", display: "block" }}
    />
  );
}

/** Store description, clamped to ~4 lines with a plain expand toggle. */
function Description({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <p
        className="t-sm"
        style={{
          margin: 0,
          color: "var(--ink-soft)",
          whiteSpace: "pre-line",
          ...(open
            ? {}
            : { display: "-webkit-box", WebkitLineClamp: 4, WebkitBoxOrient: "vertical", overflow: "hidden" }),
        }}
      >
        {text}
      </p>
      {text.length > 220 && (
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          className="font-mono"
          style={{
            marginTop: 6,
            padding: 0,
            background: "transparent",
            border: "none",
            cursor: "pointer",
            color: "var(--muted)",
            fontSize: "var(--fs-xs)",
            textDecoration: "underline",
          }}
        >
          {open ? "Show less" : "Show more"}
        </button>
      )}
    </div>
  );
}

/** One recent store review: stars, title, clamped content, author + date. */
function ReviewItem({ r, nowMs }: { r: AppReview; nowMs: number }) {
  return (
    <div style={{ borderTop: "1px solid var(--line)", paddingTop: 10 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
        {r.rating != null && (
          <span className="t-xs" style={{ color: "var(--ink)" }}>
            <Stars rating={r.rating} />
          </span>
        )}
        {r.title && <span className="t-sm w-strong">{r.title}</span>}
      </div>
      {r.content && (
        <p
          className="t-sm"
          style={{
            margin: "4px 0 0",
            color: "var(--ink-soft)",
            display: "-webkit-box",
            WebkitLineClamp: 3,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          {r.content}
        </p>
      )}
      <div className="font-mono" style={{ fontSize: "var(--fs-xs)", color: "var(--muted)", marginTop: 4 }}>
        {[r.author, r.reviewed_at ? timeAgo(r.reviewed_at, nowMs) : undefined]
          .filter(Boolean)
          .join(" · ")}
      </div>
    </div>
  );
}

/**
 * Picks the chart series for the app from the daily snapshots: Android with
 * install counts charts installs; otherwise the rating-count delta is the
 * honest download proxy (Apple publishes no download numbers).
 */
function pickSeries(
  app: AppInfo,
  points: AppMetricPoint[],
): { label: string; note?: string; unit: string; usable: AppMetricPoint[]; useInstalls: boolean } | null {
  // Group same-store points by app_id and keep the densest series, so a gem
  // with several linked apps doesn't get interleaved snapshots.
  const byApp = new Map<number, AppMetricPoint[]>();
  for (const p of points) {
    if (p.store !== app.store) continue;
    const arr = byApp.get(p.app_id);
    if (arr) arr.push(p);
    else byApp.set(p.app_id, [p]);
  }
  let mine: AppMetricPoint[] = [];
  for (const arr of byApp.values()) if (arr.length > mine.length) mine = arr;

  const useInstalls = app.store === "android" && mine.some((p) => p.installs != null);
  const usable = mine.filter((p) => (useInstalls ? p.installs != null : p.rating_count != null));
  if (usable.length === 0) return null;
  return {
    label: useInstalls ? "Installs over time" : "Ratings over time",
    note: useInstalls
      ? undefined
      : "Apple publishes no download counts — the rating count is the closest public proxy.",
    unit: useInstalls ? "installs" : "ratings",
    usable,
    useInstalls,
  };
}

/** App growth from the daily store snapshots, on the shared SketchLineChart. */
function AppGrowthChart({ app, points }: { app: AppInfo; points: AppMetricPoint[] }) {
  const series = pickSeries(app, points);
  if (!series) return null;
  const { label, note, unit, usable, useInstalls } = series;

  // The chart thins its own x labels; give every point its real date.
  const chart: ChartPoint[] = usable.map((p) => ({
    label: monthDayLabel(p.snapshot_date),
    value: (useInstalls ? p.installs : p.rating_count) ?? 0,
  }));

  const first = chart[0].value;
  const last = chart[chart.length - 1].value;
  const caption =
    usable.length < 2
      ? `Early — first snapshot ${monthDayLabel(usable[0].snapshot_date)}; trend appears after a few days.`
      : `${compactCount(first)} → ${compactCount(last)} ${unit} · ${usable.length} snapshots`;

  return (
    <div>
      <div className="t-sm w-strong" style={{ marginBottom: 2 }}>{label}</div>
      {note && <p className="t-xs" style={{ color: "var(--muted)", margin: "0 0 8px" }}>{note}</p>}
      {/* One snapshot can't draw a trend — the caption alone says it plainly. */}
      {usable.length >= 2 && (
        <SketchLineChart points={chart} height={150} highlightLast valueLabel={(v) => `${compactCount(v)} ${unit}`} />
      )}
      <p className="font-mono nums" style={{ fontSize: "var(--fs-xs)", color: "var(--muted)", marginTop: 8 }}>
        {caption}
      </p>
    </div>
  );
}

// The same product is often linked on both stores with uneven scrape coverage
// (e.g. Apple returns no screenshots while Play has the full set) — preview the
// entry with the most to show. Shared with GemHeader, which shows its identity.
export function richestApp(apps: AppInfo[]): AppInfo | undefined {
  const score = (a: AppInfo) =>
    (a.screenshots?.length ? 4 : 0) +
    (a.reviews?.length ? 2 : 0) +
    (a.installs != null ? 1 : 0) +
    (a.description ? 1 : 0);
  return [...apps].sort((a, b) => score(b) - score(a))[0];
}

export function AppStorePreview({ gem }: { gem: GemDossier }) {
  // Captured once at mount (lazy init keeps Date.now() out of render purity).
  const [nowMs] = useState(() => Date.now());
  const app = richestApp(gem.apps);
  if (!app) return null;

  const metaParts = [
    app.genre,
    app.release_date ? `Released ${isoDateLabel(app.release_date)}` : undefined,
  ].filter(Boolean);
  const reviews = (app.reviews ?? []).slice(0, 3);

  return (
    <section>
      <h2 className="t-md w-strong" style={{ margin: "0 0 12px" }}>Store listing</h2>
      <div
        style={{
          border: "1px solid var(--line)",
          background: "var(--paper)",
          padding: "18px 20px",
          display: "flex",
          flexDirection: "column",
          gap: 18,
        }}
      >
        {metaParts.length > 0 && (
          <div className="font-mono nums" style={{ fontSize: "var(--fs-xs)", color: "var(--muted)" }}>
            {metaParts.join(" · ")}
          </div>
        )}

        {app.description && <Description text={app.description} />}

        {app.screenshots && app.screenshots.length > 0 && (
          <div style={{ display: "flex", gap: 10, overflowX: "auto", paddingBottom: 6 }}>
            {app.screenshots.map((src, i) => (
              <Shot key={`${src}-${i}`} src={src} index={i} />
            ))}
          </div>
        )}

        <AppGrowthChart app={app} points={gem.app_metrics} />

        <div>
          <div className="t-sm w-strong" style={{ marginBottom: 8 }}>Recent reviews</div>
          {reviews.length === 0 ? (
            <p className="t-sm" style={{ margin: 0, color: "var(--muted)" }}>
              No written reviews yet — common for new apps.
            </p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {reviews.map((r, i) => (
                <ReviewItem key={`${r.reviewed_at ?? ""}-${r.author ?? ""}-${i}`} r={r} nowMs={nowMs} />
              ))}
            </div>
          )}
        </div>

        {app.updated_at && (
          <div
            className="font-mono"
            style={{ fontSize: "var(--fs-xs)", color: "var(--muted)", borderTop: "1px solid var(--line)", paddingTop: 10 }}
          >
            Store data checked {timeAgo(app.updated_at, nowMs)}
          </div>
        )}
      </div>
    </section>
  );
}
