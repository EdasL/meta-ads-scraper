"use client";

import Link from "next/link";
import { IconHeart, IconHeartFilled } from "@tabler/icons-react";
import { ScoreBadge } from "@/components/ui";
import { formatSettings, latestEvents, splitByRelevance, rollupByPage, type RolledResult } from "@/lib/hunt";
import { setRating } from "@/lib/api";
import type { HuntResult, HuntRound } from "@/lib/api";
import { save, unsave, useIsSaved } from "@/lib/useSaved";
import type { ShortlistRow } from "@/types/contracts";

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

/** Adapt a kept hunt result to the ShortlistRow the saved-gems store consumes.
 *  Only kept results reach the save control, so a kept advertiser id is always
 *  present. A hunt row carries no scoring axes, so the snapshot axes save as 0
 *  (unknown, not a real value); the store keeps any richer existing snapshot. */
function resultToRow(r: HuntResult): ShortlistRow {
  return {
    advertiser_id: r.kept_advertiser_id!,
    cohort: r.cohort ?? "",
    page_name: r.page_name ?? "",
    app_id: null,
    app_name: r.app_name,
    icon_url: r.icon_url ?? undefined,
    gem_score: r.gem_score ?? 0,
    scaling_velocity: 0,
    under_radar: 0,
    funnel_intent: 0,
    active_ad_count: 0,
    max_longevity_days: 0,
    alert: false,
    refreshed_at: new Date().toISOString(),
    explainer: null,
    growth: null,
    hero_creative: null,
  };
}

/** Save control: one tap saves the app to the Saved page AND records a like
 *  (feeds preference learning). Tapping again removes both. Save/unsave route
 *  through the shared saved-gems store, so /saved and the header count update
 *  immediately; the store owns the optimistic UI and reverts on a backend
 *  error, which also makes it the single source of truth for the filled state. */
function HeartButton({ result: r, userEmail }: { result: HuntResult; userEmail: string }) {
  const advertiserId = r.kept_advertiser_id!;
  const saved = useIsSaved(advertiserId);

  // The rating is a best-effort companion to the save, not part of it. The
  // save (primary action) has already landed in the store by the time this
  // runs, so a failed rating write must NOT revert it — log and move on.
  async function rate(value: "like" | "none") {
    try {
      await setRating(advertiserId, userEmail, value, "");
    } catch (e) {
      console.error("hunt heart: rating write failed", e);
    }
  }

  function toggle() {
    if (saved) {
      unsave(advertiserId);
      void rate("none");
    } else {
      save(resultToRow(r));
      void rate("like");
    }
  }

  return (
    <button
      type="button"
      onClick={toggle}
      aria-pressed={saved}
      title={saved ? "Remove from Saved" : "Save and like"}
      style={{ border: "none", background: "transparent", cursor: "pointer", padding: 4, color: saved ? "var(--down)" : "var(--muted)", flex: "0 0 auto" }}
    >
      {saved ? <IconHeartFilled size={18} /> : <IconHeart size={18} stroke={1.75} />}
    </button>
  );
}

/** One found app as a card: icon, name, store badge, GemScore, tagline, and a
 *  save control once the app is kept. All fields are real data from the round;
 *  missing fields render nothing (no placeholders). */
function ResultCard({ group: g, userEmail }: { group: RolledResult; userEmail: string | null }) {
  const r = g.rep;
  const scored = r.gem_score != null && r.kept_advertiser_id != null;
  const name = r.app_name ?? r.page_name ?? r.store_id;
  return (
    <div
      style={{
        border: "1px solid var(--line)",
        background: "var(--surface-2)",
        padding: "10px 12px",
        display: "flex",
        flexDirection: "column",
        gap: 8,
        minWidth: 0,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
        {r.icon_url ? (
          // eslint-disable-next-line @next/next/no-img-element -- store CDN icon, remote domains not configured for next/image
          <img src={r.icon_url} alt="" width={40} height={40} style={{ flex: "0 0 auto", border: "1px solid var(--line)", objectFit: "cover" }} />
        ) : (
          <span aria-hidden style={{ width: 40, height: 40, flex: "0 0 auto", border: "1px solid var(--line)", background: "var(--surface-3)" }} />
        )}
        <div style={{ minWidth: 0, flex: "1 1 auto" }}>
          {scored ? (
            <Link className="t-sm w-strong" href={`/gems/${r.kept_advertiser_id}`} style={{ color: "var(--ink)", textDecoration: "underline", display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title="Open the gem page">
              {name}
            </Link>
          ) : (
            <span className="t-sm w-strong" style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {name}
            </span>
          )}
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 2, flexWrap: "wrap" }}>
            {g.stores.map((s) => (
              <StoreBadge key={s.store} store={s.store} href={s.link_url} />
            ))}
            {scored && <ScoreBadge score={r.gem_score!} variant="card" />}
            {g.fold_count > 1 && (
              <span className="t-xs nums" style={{ color: "var(--muted)" }} title="Ads folded into this page">
                ·{g.fold_count} ads
              </span>
            )}
          </div>
        </div>
        {r.kept_advertiser_id != null && userEmail && <HeartButton result={r} userEmail={userEmail} />}
      </div>
      {r.tagline && (
        <p className="t-xs" style={{ color: "var(--ink-soft)", margin: 0, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
          {r.tagline}
        </p>
      )}
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <a className="t-xs" href={adLibraryUrl(r.page_id)} target="_blank" rel="noreferrer" style={{ color: "var(--ink-soft)", textDecoration: "underline" }} title="Open this page's ads in the Meta Ad Library">
          Ads
        </a>
        <span className="t-xs" style={{ color: "var(--muted)" }} title="Search term that surfaced this app">
          {r.source_term}
        </span>
        {r.page != null && (
          <span className="t-xs nums" style={{ color: "var(--muted)" }} title="Ad Library page this app was found on">
            p{r.page}
          </span>
        )}
        {r.was_new && (
          <span className="t-xs w-strong" style={{ color: "var(--up)" }}>
            New
          </span>
        )}
        <TriageState triage={g.triage} />
      </div>
    </div>
  );
}

/**
 * One approved round: a derived status line, a scrollable newest-first activity
 * feed, and the streaming results list. Every number is read straight from
 * `round.progress` (never restated with a derived label). An `error` round shows
 * its `error_text` plainly with no retry — a fresh round is started from chat.
 *
 * Results split by Peon's relevance judgement: on-target rows render inline,
 * off-target rows collapse into an "Off target (N)" group at the bottom, each
 * keeping the same row markup plus a muted reason line.
 */
export function RoundBlock({
  round,
  results,
  userEmail,
}: {
  round: HuntRound;
  results: HuntResult[];
  userEmail: string | null;
}) {
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
  const { onTarget, offTarget } = splitByRelevance(results);

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

      {onTarget.length > 0 && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: 10 }}>
          {rollupByPage(onTarget).map((g) => (
            <ResultCard key={g.page_id} group={g} userEmail={userEmail} />
          ))}
        </div>
      )}

      {offTarget.length > 0 && (
        <details style={{ display: "flex", flexDirection: "column" }}>
          <summary className="t-xs w-strong" style={{ color: "var(--muted)", cursor: "pointer", padding: "6px 0" }}>
            Off target ({offTarget.length})
          </summary>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: 10 }}>
            {rollupByPage(offTarget).map((g) => (
              <div key={g.page_id}>
                <ResultCard group={g} userEmail={userEmail} />
                {g.rep.relevance_note && (
                  <p className="t-xs" style={{ color: "var(--muted)", margin: "2px 0 0" }}>
                    {g.rep.relevance_note}
                  </p>
                )}
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}
