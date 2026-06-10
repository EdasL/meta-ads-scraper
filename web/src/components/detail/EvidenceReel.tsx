"use client";

import { useState } from "react";
import { Polaroid, SketchLineChart, type ChartPoint } from "@/components/sketch";
import { trendFromScaling } from "@/lib/why";
import { creativeMetaLine } from "@/lib/format";
import type { GemDossier } from "@/types/contracts";

/**
 * The proof an indie founder actually came for: the spend curve and the real ad
 * creatives. Evidence beats the math (spec §5.2). The destination ("where
 * clicks go") now lives in the GemLinks block as real, clickable links.
 */
function fmtWeek(iso: string) {
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h2 className="eyebrow" style={{ margin: "0 0 12px", color: "var(--ink)" }}>{children}</h2>;
}

/** A body-text fallback tile shown when no media (or the media fails to load). */
function TextTile({ body }: { body?: string }) {
  return (
    <div style={{ textAlign: "center", padding: 12 }}>
      <div className="t-2xl w-heavy">▣</div>
      <div className="font-mono" style={{ fontSize: "var(--fs-xs)", color: "var(--ink-soft)", marginTop: 8 }}>{body}</div>
    </div>
  );
}

const mediaStyle: React.CSSProperties = {
  width: "100%",
  height: "100%",
  objectFit: "cover",
  display: "block",
};

function CreativeTile({ c }: { c: GemDossier["creatives"][number] }) {
  const [broken, setBroken] = useState(false);
  const hasMedia = !!c.snapshot_image_url && !broken;
  const alt = c.title ?? c.body_text ?? "ad creative";
  // Per-ad facts: start date, days running, EU reach, platforms. US ads carry
  // no impressions or spend — only what Meta actually exposes is shown.
  const meta = creativeMetaLine(c);
  return (
    <div style={{ width: 158 }}>
      <Polaroid caption={c.title} width={158}>
        {hasMedia ? (
          c.type === "video" ? (
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
          )
        ) : (
          <TextTile body={c.body_text} />
        )}
      </Polaroid>
      {meta && (
        <div className="font-mono nums" style={{ fontSize: "0.62rem", color: "var(--muted)", marginTop: 5, lineHeight: 1.35 }}>
          {meta}
        </div>
      )}
    </div>
  );
}

export function EvidenceReel({ gem }: { gem: GemDossier }) {
  const chart: ChartPoint[] = gem.scaling.map((s) => ({ label: fmtWeek(s.week_start), value: s.active_ad_count }));
  const t = trendFromScaling(gem.scaling);
  const caption =
    t.multiple != null
      ? `${gem.scaling[0]?.active_ad_count} → ${gem.scaling[gem.scaling.length - 1]?.active_ad_count} ads over ${gem.scaling.length} weeks · ${t.multiple}x`
      : "Early — not many weeks of data yet.";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 30 }}>
      <section>
        <SectionTitle>Ad growth — ads per week</SectionTitle>
        <SketchLineChart points={chart} height={190} highlightLast />
        <p className="font-mono nums" style={{ fontSize: "var(--fs-xs)", color: "var(--muted)", marginTop: 8 }}>{caption}</p>
      </section>

      <section>
        <SectionTitle>The ads</SectionTitle>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 14 }}>
          {gem.creatives.map((c) => (
            <CreativeTile key={c.id} c={c} />
          ))}
        </div>
      </section>
    </div>
  );
}
