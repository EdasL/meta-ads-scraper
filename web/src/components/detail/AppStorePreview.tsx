"use client";

import { useState } from "react";
import { IconExternalLink } from "@tabler/icons-react";
import { buildGemLinks } from "@/lib/links";
import { compactCount, isoDateLabel, monthDayLabel, timeAgo } from "@/lib/format";
import { SketchLineChart, type ChartPoint } from "@/components/sketch";
import type { AppInfo, AppMetricPoint, AppReview, GemDossier } from "@/types/contracts";

/**
 * "THE APP" — the in-UI store preview for the gem's app, plus the app-side
 * growth chart. Everything is conditional on what the store actually returned;
 * Apple publishes no download counts, so the iOS trend is the rating-count
 * delta, labeled as a proxy. Plain professional voice throughout.
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
 * Three-way store-presence chips for the detail header: iOS APP / ANDROID APP,
 * or WEB ONLY when the gem has no linked store listing.
 */
export function StorePresenceChips({ apps }: { apps: AppInfo[] }) {
  const labels: string[] = [];
  if (apps.some((a) => a.store === "ios")) labels.push("iOS APP");
  if (apps.some((a) => a.store === "android")) labels.push("ANDROID APP");
  if (labels.length === 0) labels.push("WEB ONLY");
  return (
    <span style={{ display: "inline-flex", gap: 6 }}>
      {labels.map((l) => (
        <span
          key={l}
          className="font-mono"
          style={{
            fontSize: "var(--fs-xs)",
            letterSpacing: "0.08em",
            textTransform: "none",
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

/** App icon; hides itself if the image fails to load. */
function AppIcon({ src, name }: { src: string; name: string }) {
  const [broken, setBroken] = useState(false);
  if (broken) return null;
  return (
    // eslint-disable-next-line @next/next/no-img-element -- external store CDN images; next/image is a poor fit for an internal tool
    <img
      src={src}
      alt={`${name} icon`}
      width={56}
      height={56}
      loading="lazy"
      onError={() => setBroken(true)}
      style={{ border: "1.5px solid var(--ink)", display: "block", objectFit: "cover", flex: "0 0 auto" }}
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
          className="font-mono caps"
          style={{
            marginTop: 6,
            padding: 0,
            background: "transparent",
            border: "none",
            cursor: "pointer",
            color: "var(--muted)",
            fontSize: "var(--fs-xs)",
            letterSpacing: "0.08em",
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
): { label: string; unit: string; usable: AppMetricPoint[]; useInstalls: boolean } | null {
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
    label: useInstalls ? "installs over time" : "ratings over time (download proxy)",
    unit: useInstalls ? "installs" : "ratings",
    usable,
    useInstalls,
  };
}

/** App growth from the daily store snapshots, on the shared SketchLineChart. */
function AppGrowthChart({ app, points }: { app: AppInfo; points: AppMetricPoint[] }) {
  const series = pickSeries(app, points);
  if (!series) return null;
  const { label, unit, usable, useInstalls } = series;

  // Thin the x-axis labels so a month of daily snapshots stays readable.
  const every = Math.max(1, Math.ceil(usable.length / 6));
  const chart: ChartPoint[] = usable.map((p, i) => ({
    label: i % every === 0 || i === usable.length - 1 ? monthDayLabel(p.snapshot_date) : "",
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
      <div className="eyebrow" style={{ color: "var(--ink)", marginBottom: 8 }}>{label}</div>
      <SketchLineChart points={chart} height={150} highlightLast />
      <p className="font-mono nums" style={{ fontSize: "var(--fs-xs)", color: "var(--muted)", marginTop: 8 }}>
        {caption}
      </p>
    </div>
  );
}

// The same product is often linked on both stores with uneven scrape coverage
// (e.g. Apple returns no screenshots while Play has the full set) — preview the
// entry with the most to show.
function richestApp(apps: AppInfo[]): AppInfo | undefined {
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

  const name = app.name ?? gem.app_name ?? gem.page_name;
  const storeLink = buildGemLinks(gem).find(
    (l) => l.label === "app store" || l.label === "play store",
  );
  const metaParts = [
    app.genre,
    app.price_label,
    app.release_date ? `released ${isoDateLabel(app.release_date)}` : undefined,
  ].filter(Boolean);
  const reviews = (app.reviews ?? []).slice(0, 3);

  return (
    <section>
      <h2 className="eyebrow" style={{ margin: "0 0 12px", color: "var(--ink)" }}>the app</h2>
      <div
        style={{
          border: "1.5px solid var(--ink)",
          background: "var(--paper)",
          boxShadow: "var(--shadow-hard)",
          padding: "18px 20px",
          display: "flex",
          flexDirection: "column",
          gap: 18,
        }}
      >
        {/* identity: icon + name + developer + rating */}
        <div style={{ display: "flex", gap: 14, alignItems: "flex-start", flexWrap: "wrap" }}>
          {app.icon_url && <AppIcon src={app.icon_url} name={name} />}
          <div style={{ flex: "1 1 200px", minWidth: 0 }}>
            <div className="t-lg w-heavy" style={{ wordBreak: "break-word" }}>{name}</div>
            {app.developer && (
              <div className="t-sm" style={{ color: "var(--muted)", marginTop: 2 }}>{app.developer}</div>
            )}
            {app.rating != null && (
              <div className="font-mono nums t-sm" style={{ marginTop: 6, color: "var(--ink-soft)" }}>
                <Stars rating={app.rating} /> {app.rating.toFixed(1)}
                {app.rating_count != null && ` (${app.rating_count.toLocaleString("en-US")})`}
              </div>
            )}
          </div>
          {storeLink && (
            <a
              href={storeLink.href}
              target="_blank"
              rel="noopener noreferrer"
              className="font-mono caps"
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 7,
                padding: "9px 14px",
                border: "1.5px solid var(--ink)",
                background: "var(--paper)",
                color: "var(--ink)",
                textDecoration: "none",
                fontSize: "var(--fs-xs)",
                letterSpacing: "0.08em",
                whiteSpace: "nowrap",
              }}
            >
              {storeLink.label}
              <IconExternalLink size={14} stroke={2} />
            </a>
          )}
        </div>

        {/* genre · price · release date, plus installs when the store has them */}
        {(metaParts.length > 0 || app.installs != null) && (
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {metaParts.length > 0 && (
              <div className="font-mono nums" style={{ fontSize: "var(--fs-xs)", color: "var(--muted)" }}>
                {metaParts.join(" · ")}
              </div>
            )}
            {app.installs != null && (
              <div className="font-mono nums t-sm" style={{ color: "var(--ink-soft)" }}>
                ≈{compactCount(app.installs)} installs · Play
              </div>
            )}
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
          <div className="eyebrow" style={{ color: "var(--ink)", marginBottom: 8 }}>recent reviews</div>
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
