"use client";

import { useState } from "react";
import Link from "next/link";
import { IconHeart, IconHeartFilled } from "@tabler/icons-react";
import { ScoreBadge } from "@/components/ui";
import { formatSettings, latestEvents, splitByRelevance } from "@/lib/hunt";
import { removeSavedGem, setRating, upsertSavedGem } from "@/lib/api";
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

/** Save control: one tap saves the app to the Saved page AND records a like
 *  (feeds preference learning). Tapping again removes both. Optimistic with
 *  revert; the 2s round poll re-syncs `saved` from the server on any drift. */
function HeartButton({ result: r, userEmail }: { result: HuntResult; userEmail: string }) {
  const [saved, setSaved] = useState(r.saved);
  const [busy, setBusy] = useState(false);
  const advertiserId = r.kept_advertiser_id!;

  // Re-sync from the server value when the 2s round poll delivers a change.
  // Adjusting state during render (guarded by the change) is React's recommended
  // replacement for a setState-in-effect — it won't fight an in-flight optimistic
  // toggle, since the server value only differs once the write has landed.
  const [prevServerSaved, setPrevServerSaved] = useState(r.saved);
  if (r.saved !== prevServerSaved) {
    setPrevServerSaved(r.saved);
    setSaved(r.saved);
  }

  async function toggle() {
    if (busy) return;
    const next = !saved;
    setSaved(next);
    setBusy(true);
    try {
      if (next) {
        await upsertSavedGem(advertiserId, {
          user_email: userEmail,
          page_name: r.page_name,
          app_name: r.app_name,
          icon_url: r.icon_url,
          cohort: r.cohort ?? "",
          gem_score: r.gem_score ?? 0,
          scaling_velocity: null,
          under_radar: null,
          funnel_intent: null,
          active_ad_count: null,
          tags: [],
          note: "",
          stage: "just_saw",
          verdict: null,
          confidence: null,
        });
        await setRating(advertiserId, userEmail, "like", "");
      } else {
        await removeSavedGem(advertiserId, userEmail);
        await setRating(advertiserId, userEmail, "none", "");
      }
    } catch {
      setSaved(!next); // revert; the next poll re-syncs from the server
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      type="button"
      onClick={toggle}
      disabled={busy}
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
function ResultCard({ result: r, userEmail }: { result: HuntResult; userEmail: string | null }) {
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
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 2 }}>
            <StoreBadge store={r.store} href={r.link_url} />
            {scored && <ScoreBadge score={r.gem_score!} variant="card" />}
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
        <TriageState triage={r.triage} />
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
          {onTarget.map((r) => (
            <ResultCard key={r.id} result={r} userEmail={userEmail} />
          ))}
        </div>
      )}

      {offTarget.length > 0 && (
        <details style={{ display: "flex", flexDirection: "column" }}>
          <summary className="t-xs w-strong" style={{ color: "var(--muted)", cursor: "pointer", padding: "6px 0" }}>
            Off target ({offTarget.length})
          </summary>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: 10 }}>
            {offTarget.map((r) => (
              <div key={r.id}>
                <ResultCard result={r} userEmail={userEmail} />
                {r.relevance_note && (
                  <p className="t-xs" style={{ color: "var(--muted)", margin: "2px 0 0" }}>
                    {r.relevance_note}
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
