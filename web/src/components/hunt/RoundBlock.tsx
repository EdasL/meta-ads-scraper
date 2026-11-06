"use client";

import Link from "next/link";
import { useState } from "react";
import { AppIcon, Button } from "@/components/ui";
import { formatSettings, latestEvents, reviewGroups, type RolledResult } from "@/lib/hunt";
import { rememberOrigin } from "@/lib/returnTo";
import type { HuntResult, HuntRound } from "@/lib/api";
import { FolderPicker } from "@/components/saved/FolderPicker";
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
        title="Open the advertised destination"
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

/** The unified heart: saving + liking + folder filing in one control. An
 *  unsaved result saves and records a like on press, then the folder picker
 *  opens; a saved one goes straight to the picker (unsave lives inside it).
 *  resultToRow adapts the hunt result for the store's save snapshot. */
function HeartButton({ result: r }: { result: HuntResult }) {
  return <FolderPicker advertiserId={r.kept_advertiser_id!} row={resultToRow(r)} variant="heart" size="sm" />;
}

/** One found app as a card: icon, name, store badge, GemScore, tagline, and a
 *  save control once the app is kept. All fields are real data from the round;
 *  a missing icon falls back to the house initials tile (AppIcon), a missing
 *  tagline renders nothing. */
function ResultCard({ group: g, userEmail }: { group: RolledResult; userEmail: string | null }) {
  const r = g.rep;
  const scored = r.gem_score != null && r.kept_advertiser_id != null;
  const name = r.page_name ?? r.app_name ?? r.store_id;
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
        <AppIcon src={r.icon_url ?? undefined} name={name} size={40} />
        <div style={{ minWidth: 0, flex: "1 1 auto" }}>
          {scored ? (
            <Link className="t-sm w-strong" href={`/gems/${r.kept_advertiser_id}`} onClick={rememberOrigin} style={{ color: "var(--ink)", textDecoration: "underline", display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title="Open the gem page">
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

          </div>
        </div>
        {r.kept_advertiser_id != null && userEmail && <HeartButton result={r} />}
      </div>
      <span className="t-xs w-strong" style={{ color: g.relevance === "relevant" ? "var(--up)" : "var(--muted)" }}>
        {g.relevance === "relevant" ? "Relevant" : g.relevance === "off_target" ? "Off target" : "Needs review"}
      </span>
      <p className="t-xs" style={{ color: "var(--ink-soft)", margin: 0 }}>{g.relevance_note}</p>
      {g.ad_evidence.length > 0 && (
        <details>
          <summary className="t-xs" style={{ cursor: "pointer", color: "var(--ink-soft)" }}>Read captured ad copy</summary>
          <div style={{ maxHeight: 300, overflowY: "auto" }}>
          {g.ad_evidence.map((ad) => (
            <blockquote key={ad.ad_archive_id} className="t-xs" style={{ margin: "8px 0", paddingLeft: 10, borderLeft: "2px solid var(--line)", whiteSpace: "pre-wrap" }}>
              {ad.body_text}
              <a href={`https://www.facebook.com/ads/library/?id=${encodeURIComponent(ad.ad_archive_id)}`} target="_blank" rel="noreferrer" style={{ display: "block", marginTop: 6, textDecoration: "underline" }}>Open this ad</a>
            </blockquote>
          ))}
          </div>
        </details>
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
      </div>
    </div>
  );
}

/** One round, with a bounded advertiser shortlist and inspectable evidence. */
export function RoundBlock({
  round,
  results,
  userEmail,
}: {
  round: HuntRound;
  results: HuntResult[];
  userEmail: string | null;
}) {
  const [view, setView] = useState<"relevant" | "uncertain" | "off_target">("relevant");
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(12);
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
  const groups = reviewGroups(results);
  const term = query.trim().toLowerCase();
  const visible = term
    ? [...groups.relevant, ...groups.uncertain, ...groups.off_target].filter((g) =>
        [g.rep.page_name, g.rep.app_name, ...g.stores.map((s) => s.link_url)].some((v) => v?.toLowerCase().includes(term)))
    : groups[view];
  const labels = { relevant: "Relevant", uncertain: "Needs review", off_target: "Off target" } as const;

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
        <details>
          <summary className="t-xs" style={{ cursor: "pointer", color: "var(--muted)" }}>Search activity</summary>
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
        </details>
      )}

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        {(Object.keys(labels) as (keyof typeof labels)[]).map((key) => (
          <button key={key} type="button" aria-pressed={!term && view === key}
            onClick={() => { setView(key); setQuery(""); setLimit(12); }}
            className="t-xs w-strong"
            style={{ padding: "8px 12px", cursor: "pointer", border: "1px solid var(--line)",
              background: !term && view === key ? "var(--ink)" : "var(--surface-2)",
              color: !term && view === key ? "var(--surface-1)" : "var(--ink-soft)" }}>
            {labels[key]} ({groups[key].length})
          </button>
        ))}
        <input aria-label="Search all advertisers" placeholder="Search all advertisers"
          value={query} onChange={(e) => { setQuery(e.target.value); setLimit(12); }}
          className="t-xs" style={{ padding: "8px 10px", border: "1px solid var(--line)",
            background: "var(--surface-1)", color: "var(--ink)", flex: "1 1 180px", minWidth: 0 }} />
      </div>
      <p className="t-xs" style={{ margin: 0, color: "var(--muted)" }}>
        {term ? `Search results: ${visible.length} ${visible.length === 1 ? "advertiser" : "advertisers"}.`
          : view === "relevant" ? "Matches for the advertised need and audience. Open the ads to assess the creative."
          : view === "uncertain" ? "Missing or conflicting evidence. These advertisers are still available to review."
          : "Assessed as a different offer or audience. Review the reason if a result looks misplaced."}
      </p>
      {visible.length === 0 ? (
        <p className="t-sm" style={{ margin: "8px 0", color: "var(--ink-soft)" }}>
          {term ? "No advertisers match this search."
            : view === "relevant" ? "No assessed matches yet. Check Needs review for unassessed advertisers."
            : "No advertisers in this group."}
        </p>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(260px, 100%), 1fr))", alignItems: "start", gap: 10 }}>
          {visible.slice(0, limit).map((g) => <ResultCard key={g.page_id} group={g} userEmail={userEmail} />)}
        </div>
      )}
      {visible.length > limit && (
        <Button variant="ghost" onClick={() => setLimit((n) => n + 12)}>
          Show more ({visible.length - limit} remaining)
        </Button>
      )}
    </div>
  );
}
