"use client";

import { useState } from "react";
import { Polaroid, SketchConnector, SketchLineChart, type ChartPoint } from "@/components/sketch";
import { INK } from "@/components/sketch/palette";
import { trendFromScaling } from "@/lib/why";
import type { GemDossier } from "@/types/contracts";

/**
 * The proof an indie founder actually came for: the spend curve, the real ad
 * creatives, and the funnel they run. Evidence beats the math (spec §5.2).
 */
function fmtWeek(iso: string) {
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h2 className="eyebrow" style={{ margin: "0 0 12px", color: "var(--ink)" }}>{children}</h2>;
}

/** Funnel-step screenshot (served by /api/media); hides itself if it 404s. */
function FunnelShot({ url, label }: { url: string; label: string }) {
  const [broken, setBroken] = useState(false);
  if (broken) return null;
  return (
    // eslint-disable-next-line @next/next/no-img-element -- dynamic /api/media proxy thumbnails; next/image is a poor fit for an internal tool
    <img src={url} alt={label} loading="lazy" onError={() => setBroken(true)} style={{ width: "100%", marginTop: 8, border: `1px solid ${INK}`, display: "block" }} />
  );
}

function CreativeTile({ c }: { c: GemDossier["creatives"][number] }) {
  const [broken, setBroken] = useState(false);
  const showImage = c.type === "image" && !!c.snapshot_image_url && !broken;
  return (
    <Polaroid caption={c.title} width={158}>
      {showImage ? (
        // eslint-disable-next-line @next/next/no-img-element -- dynamic /api/media proxy thumbnails; next/image is a poor fit for an internal tool
        <img src={c.snapshot_image_url} alt={c.title ?? c.body_text ?? "ad creative"} loading="lazy" onError={() => setBroken(true)} style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }} />
      ) : (
        <div style={{ textAlign: "center", padding: 12 }}>
          <div className="t-2xl w-heavy">{c.type === "video" ? "▶" : "▣"}</div>
          <div className="font-mono" style={{ fontSize: "var(--fs-xs)", color: "var(--ink-soft)", marginTop: 8 }}>{c.body_text}</div>
        </div>
      )}
    </Polaroid>
  );
}

function FunnelChain({ steps }: { steps: GemDossier["funnel"] }) {
  return (
    <div>
      {steps.map((s, i) => (
        <div key={s.step}>
          <div style={{ border: `1.5px solid ${INK}`, background: "var(--paper)", padding: "12px 16px" }}>
            <div className="eyebrow" style={{ color: "var(--ink)" }}>step {s.step}</div>
            <div className="t-lg w-strong caps" style={{ marginTop: 2 }}>{s.label}</div>
            <div className="font-mono" style={{ fontSize: "var(--fs-xs)", color: "var(--muted)", wordBreak: "break-all", marginTop: 3 }}>
              {s.final_url ?? s.landing_url}
            </div>
            {s.screenshot_url && <FunnelShot url={s.screenshot_url} label={s.label} />}
          </div>
          {i < steps.length - 1 && (
            <div style={{ display: "flex", justifyContent: "center" }}>
              <SketchConnector width={40} height={34} color={INK} strokeWidth={2} points={[[20, 2], [20, 32]]} arrow />
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

/** Plain checklist of what makes the funnel "real" (truthful from the data). */
function FunnelChecklist({ gem }: { gem: GemDossier }) {
  const hasLanding = gem.funnel.length > 0;
  const hasPaywall = gem.funnel.some((s) => /paywall|trial|pro|join|go|quiz|chat/i.test(s.label)) || !!gem.pricing;
  const hasStore = gem.funnel.some((s) => /apps\.apple|play\.google/i.test(s.final_url ?? ""));
  const items: [string, boolean][] = [
    ["landing", hasLanding],
    ["paywall", hasPaywall],
    [gem.pricing ? `price ${gem.pricing}` : "price", !!gem.pricing],
    [hasStore ? "to app store" : "to site", true],
  ];
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 14px", marginBottom: 12 }}>
      {items.map(([label, ok]) => (
        <span key={label} className="font-mono" style={{ fontSize: "var(--fs-xs)", color: ok ? "var(--ink)" : "var(--muted)" }}>
          {ok ? "✓" : "·"} {label}
        </span>
      ))}
    </div>
  );
}

export function EvidenceReel({ gem }: { gem: GemDossier }) {
  const chart: ChartPoint[] = gem.scaling.map((s) => ({ label: fmtWeek(s.week_start), value: s.active_ad_count }));
  const t = trendFromScaling(gem.scaling);
  const caption =
    t.multiple != null
      ? `${gem.scaling[0]?.active_ad_count} → ${gem.scaling[gem.scaling.length - 1]?.active_ad_count} ad over ${gem.scaling.length} week · ${t.multiple}x`
      : "early — not many week yet.";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 30 }}>
      <section>
        <SectionTitle>how fast it grow — ads per week</SectionTitle>
        <SketchLineChart points={chart} height={190} highlightLast />
        <p className="font-mono nums" style={{ fontSize: "var(--fs-xs)", color: "var(--muted)", marginTop: 8 }}>{caption}</p>
      </section>

      <section>
        <SectionTitle>where click go</SectionTitle>
        <FunnelChecklist gem={gem} />
        <FunnelChain steps={gem.funnel} />
      </section>

      <section>
        <SectionTitle>the ads</SectionTitle>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 14 }}>
          {gem.creatives.map((c) => (
            <CreativeTile key={c.id} c={c} />
          ))}
        </div>
      </section>
    </div>
  );
}
