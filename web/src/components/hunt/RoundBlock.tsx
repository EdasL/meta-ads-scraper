"use client";

import Link from "next/link";
import { ScoreBadge } from "@/components/ui";
import { formatSettings, latestEvents } from "@/lib/hunt";
import type { HuntResult, HuntRound } from "@/lib/api";

/** Human label for a result's store. Unknown stores fall back to the raw value. */
function storeLabel(store: string): string {
  if (store === "ios" || store === "apple") return "App Store";
  if (store === "android" || store === "google") return "Play Store";
  if (store === "web") return "Web";
  return store;
}

/**
 * Bordered mono chip — the app's store presence, mirroring StorePresenceChips.
 * Links to the store listing (the ad's destination URL) when one was captured.
 */
function StoreBadge({ store, href }: { store: string; href?: string | null }) {
  const style = {
    fontSize: "var(--fs-xs)",
    padding: "1px 8px",
    border: "1px solid var(--ink)",
    color: "var(--ink-soft)",
    whiteSpace: "nowrap",
  } as const;
  if (href) {
    return (
      <a
        className="font-mono"
        style={{ ...style, textDecoration: "none" }}
        href={href}
        target="_blank"
        rel="noreferrer"
        title="Open the store listing"
      >
        {storeLabel(store)}
      </a>
    );
  }
  return (
    <span className="font-mono" style={style}>
      {storeLabel(store)}
    </span>
  );
}

/** Meta Ad Library URL for a page — the found app's live ads, viewable before enrich. */
function adLibraryUrl(pageId: string): string {
  return `https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=US&view_all_page_id=${encodeURIComponent(pageId)}`;
}

/** Triage state as plain, color-stepped text (spec copy: checking / kept / vetoed). */
function TriageState({ triage }: { triage: HuntResult["triage"] }) {
  const { label, color } =
    triage === "kept"
      ? { label: "kept", color: "var(--up)" }
      : triage === "vetoed"
        ? { label: "vetoed", color: "var(--muted)" }
        : { label: "checking", color: "var(--warn)" };
  return (
    <span className="t-xs w-strong" style={{ color }}>
      {label}
    </span>
  );
}

/**
 * One approved round: a derived status line, a scrollable newest-first activity
 * feed, and the streaming results list. Every number is read straight from
 * `round.progress` (never restated with a derived label). An `error` round shows
 * its `error_text` plainly with no retry — a fresh round is started from chat.
 */
export function RoundBlock({ round, results }: { round: HuntRound; results: HuntResult[] }) {
  const p = round.progress;
  const events = latestEvents(round);

  // Status line derived from real progress counters, e.g. "3 lanes · 6 of 12 terms".
  let statusLine: string;
  if (round.status === "queued") {
    statusLine = "Queued";
  } else {
    const parts: string[] = [];
    const laneCount = p.lanes ? Object.keys(p.lanes).length : 0;
    if (laneCount > 0) parts.push(`${laneCount} lanes`);
    if (p.terms_total != null) parts.push(`${p.terms_done ?? 0} of ${p.terms_total} terms`);
    statusLine = parts.length ? parts.join(" · ") : round.status === "running" ? "Running" : "Done";
  }

  // Append the round's non-default scrape settings (plain, real stored values).
  const settingsLabel = formatSettings(round.settings);
  if (settingsLabel) statusLine += ` · ${settingsLabel}`;

  const showCounters = p.found != null || p.new != null;

  return (
    <div
      style={{
        border: "1.5px solid var(--ink)",
        background: "var(--surface-1)",
        boxShadow: "var(--shadow-hard)",
        padding: "16px 18px",
        marginTop: 10,
        display: "flex",
        flexDirection: "column",
        gap: 12,
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        <span className="t-sm w-strong nums">{statusLine}</span>
        {showCounters && (
          <span className="t-xs nums" style={{ color: "var(--muted)" }}>
            {(p.found ?? 0)} found · {(p.new ?? 0)} new to Field Desk
          </span>
        )}
      </div>

      {round.status === "error" && (
        <p className="t-sm" style={{ color: "var(--down)", margin: 0 }}>
          {round.error_text ?? "This round failed."}
        </p>
      )}

      {events.length > 0 && (
        <div
          style={{
            maxHeight: 160,
            overflowY: "auto",
            border: "1px solid var(--line)",
            background: "var(--surface-2)",
            padding: "8px 10px",
            display: "flex",
            flexDirection: "column",
            gap: 4,
          }}
        >
          {events.map((e, i) => (
            <div key={`${e.t}-${i}`} className="t-xs font-mono" style={{ color: "var(--ink-soft)" }}>
              {e.msg}
            </div>
          ))}
        </div>
      )}

      {results.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
          {results.map((r) => {
            const scored = r.gem_score != null && r.kept_advertiser_id != null;
            return (
              <div
                key={r.id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  padding: "7px 0",
                  borderTop: "1px solid var(--line)",
                  flexWrap: "wrap",
                }}
              >
                {scored ? (
                  <Link
                    className="t-sm w-strong"
                    href={`/gems/${r.kept_advertiser_id}`}
                    style={{ flex: "1 1 180px", minWidth: 0, color: "var(--ink)", textDecoration: "underline" }}
                    title="Open the gem page"
                  >
                    {r.page_name ?? r.store_id}
                  </Link>
                ) : (
                  <span className="t-sm w-strong" style={{ flex: "1 1 180px", minWidth: 0 }}>
                    {r.page_name ?? r.store_id}
                  </span>
                )}
                <StoreBadge store={r.store} href={r.link_url} />
                <a
                  className="t-xs"
                  href={adLibraryUrl(r.page_id)}
                  target="_blank"
                  rel="noreferrer"
                  style={{ color: "var(--ink-soft)", textDecoration: "underline" }}
                  title="Open this page's ads in the Meta Ad Library"
                >
                  Ads
                </a>
                <span className="t-xs" style={{ color: "var(--muted)" }} title="Search term that surfaced this app">
                  {r.source_term}
                </span>
                {r.was_new && (
                  <span className="t-xs w-strong" style={{ color: "var(--up)" }}>
                    New
                  </span>
                )}
                <TriageState triage={r.triage} />
                {scored && (
                  <Link href={`/gems/${r.kept_advertiser_id}`} title={`GemScore ${Math.round(r.gem_score!)} / 100`} style={{ textDecoration: "none" }}>
                    <ScoreBadge score={r.gem_score!} variant="card" />
                  </Link>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
