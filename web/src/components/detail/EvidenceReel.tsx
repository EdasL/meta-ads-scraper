"use client";

import { useState } from "react";
import { SketchLineChart, type ChartPoint } from "@/components/sketch";
import { trendFromScaling } from "@/lib/why";
import { adRunLine, creativeMetaLine } from "@/lib/format";
import { adLibraryAdUrl, bareDomain } from "@/lib/links";
import type { GemDossier } from "@/types/contracts";

/**
 * The proof an indie founder actually came for: the spend curve and the real ad
 * record. One card per ad, the Ad Library way: status, run dates, the creative
 * (or the ad copy when Meta no longer serves media for an ended ad), CTA, and a
 * link to the ad's own library page. The destination ("where clicks go") lives
 * in the GemLinks block as real, clickable links.
 */
function fmtWeek(iso: string) {
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

/** How many ad cards render before the "Show all" expander. */
const INITIAL_ADS = 24;

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h2 className="eyebrow" style={{ margin: "0 0 12px", color: "var(--ink)" }}>{children}</h2>;
}

function StatusPill({ active }: { active: boolean | undefined }) {
  // Unknown status (older rows without is_active) shows nothing — never guess.
  if (active === undefined) return null;
  return (
    <span
      className="font-mono caps"
      style={{
        fontSize: "0.6rem",
        letterSpacing: "0.08em",
        padding: "2px 7px",
        border: "1px solid",
        borderColor: active ? "var(--ink)" : "var(--line)",
        background: active ? "var(--ink)" : "transparent",
        color: active ? "var(--paper)" : "var(--muted)",
      }}
    >
      {active ? "Active" : "Ended"}
    </span>
  );
}

const mediaStyle: React.CSSProperties = {
  width: "100%",
  height: 170,
  objectFit: "cover",
  display: "block",
  borderTop: "1px solid var(--line)",
  borderBottom: "1px solid var(--line)",
};

function AdCard({ c }: { c: GemDossier["creatives"][number] }) {
  const [broken, setBroken] = useState(false);
  const hasMedia = !!c.snapshot_image_url && !broken;
  const alt = c.title ?? c.body_text ?? "ad creative";
  const run = adRunLine(c);
  // Reach + platforms only — US ads carry neither, so this often stays empty.
  const facts = creativeMetaLine({
    eu_total_reach: c.eu_total_reach,
    publisher_platforms: c.publisher_platforms,
  });
  const ctaDomain = c.link_url ? bareDomain(c.link_url).split("/")[0] : "";
  return (
    <div
      style={{
        border: "1.5px solid var(--ink)",
        background: "var(--paper)",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, padding: "8px 10px" }}>
        <StatusPill active={c.ad_active} />
        {c.external_ad_id && (
          <a
            href={adLibraryAdUrl(c.external_ad_id)}
            target="_blank"
            rel="noopener noreferrer"
            className="font-mono"
            style={{ fontSize: "0.6rem", color: "var(--muted)", textDecoration: "underline" }}
          >
            Ad Library ↗
          </a>
        )}
      </div>
      {run && (
        <div className="font-mono nums" style={{ fontSize: "0.66rem", color: "var(--ink-soft)", padding: "0 10px 8px" }}>
          {run}
        </div>
      )}
      {hasMedia &&
        (c.type === "video" ? (
          <video
            src={c.snapshot_image_url}
            controls
            preload="metadata"
            playsInline
            onError={() => setBroken(true)}
            style={mediaStyle}
          />
        ) : (
          // eslint-disable-next-line @next/next/no-img-element -- dynamic /api/media proxy thumbnails; next/image is a poor fit for an internal tool
          <img src={c.snapshot_image_url} alt={alt} loading="lazy" onError={() => setBroken(true)} style={mediaStyle} />
        ))}
      {c.body_text && (
        <div
          style={{
            fontSize: "var(--fs-xs)",
            color: "var(--ink-soft)",
            padding: "8px 10px",
            lineHeight: 1.45,
            display: "-webkit-box",
            WebkitLineClamp: 4,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          {c.body_text}
        </div>
      )}
      {facts && (
        <div className="font-mono nums" style={{ fontSize: "0.62rem", color: "var(--muted)", padding: "0 10px 8px" }}>
          {facts}
        </div>
      )}
      {(c.cta_text || ctaDomain) && (
        <div
          style={{
            marginTop: "auto",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 8,
            padding: "8px 10px",
            borderTop: "1px solid var(--line)",
          }}
        >
          <span className="font-mono" style={{ fontSize: "0.62rem", color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {ctaDomain}
          </span>
          {c.cta_text && (
            <span className="font-mono caps" style={{ fontSize: "0.6rem", padding: "3px 8px", border: "1px solid var(--ink)", whiteSpace: "nowrap" }}>
              {c.cta_text}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

export function EvidenceReel({ gem }: { gem: GemDossier }) {
  const [showAll, setShowAll] = useState(false);
  const chart: ChartPoint[] = gem.scaling.map((s) => ({ label: fmtWeek(s.week_start), value: s.active_ad_count }));
  const t = trendFromScaling(gem.scaling);
  const caption =
    t.multiple != null
      ? `${gem.scaling[0]?.active_ad_count} → ${gem.scaling[gem.scaling.length - 1]?.active_ad_count} ads over ${gem.scaling.length} weeks · ${t.multiple}x`
      : "Early — not many weeks of data yet.";

  const total = gem.creatives.length;
  const activeCount = gem.creatives.filter((c) => c.ad_active === true).length;
  const endedCount = gem.creatives.filter((c) => c.ad_active === false).length;
  const counts = [
    `${total} ads collected`,
    activeCount > 0 || endedCount > 0 ? `${activeCount} active now` : null,
    endedCount > 0 ? `${endedCount} ended` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const visible = showAll ? gem.creatives : gem.creatives.slice(0, INITIAL_ADS);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 30 }}>
      <section>
        <SectionTitle>Ad growth — ads per week</SectionTitle>
        <SketchLineChart points={chart} height={190} highlightLast />
        <p className="font-mono nums" style={{ fontSize: "var(--fs-xs)", color: "var(--muted)", marginTop: 8 }}>{caption}</p>
      </section>

      <section>
        <SectionTitle>The ads</SectionTitle>
        {total > 0 && (
          <p className="font-mono nums" style={{ fontSize: "var(--fs-xs)", color: "var(--muted)", margin: "0 0 12px" }}>
            {counts}
          </p>
        )}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(210px, 1fr))", gap: 14 }}>
          {visible.map((c) => (
            <AdCard key={c.id} c={c} />
          ))}
        </div>
        {total > INITIAL_ADS && (
          <button
            type="button"
            onClick={() => setShowAll((s) => !s)}
            className="font-mono caps"
            style={{
              marginTop: 14,
              padding: "8px 14px",
              fontSize: "var(--fs-xs)",
              border: "1.5px solid var(--ink)",
              background: "transparent",
              color: "var(--ink)",
              cursor: "pointer",
            }}
          >
            {showAll ? "Show fewer" : `Show all ${total} ads`}
          </button>
        )}
      </section>
    </div>
  );
}
