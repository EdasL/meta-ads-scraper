"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { getGem } from "@/lib/api";
import { Stamp, Polaroid, SketchConnector, SketchLineChart, type ChartPoint } from "@/components/sketch";
import { Fallback } from "@/components/Fallback";
import { INK } from "@/components/sketch/palette";
import { IconArrowLeft } from "@tabler/icons-react";
import type { GemDossier } from "@/types/contracts";

function fmtWeek(iso: string) {
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h3 className="eyebrow" style={{ margin: "0 0 12px", color: INK }}>{children}</h3>;
}

/** Funnel-step screenshot (from the /api/media proxy); hides itself if it 404s. */
function FunnelShot({ url, label }: { url: string; label: string }) {
  const [broken, setBroken] = useState(false);
  if (broken) return null;
  return (
    // eslint-disable-next-line @next/next/no-img-element -- dynamic /api/media proxy thumbnails; next/image optimizer is a poor fit for an internal tool
    <img
      src={url}
      alt={label}
      loading="lazy"
      onError={() => setBroken(true)}
      style={{ width: "100%", marginTop: 8, border: `1px solid ${INK}`, display: "block" }}
    />
  );
}

function FunnelChain({ steps }: { steps: GemDossier["funnel"] }) {
  return (
    <div>
      {steps.map((s, i) => (
        <div key={s.step}>
          <div style={{ border: `1.5px solid ${INK}`, background: "var(--paper)", padding: "12px 16px" }}>
            <div className="eyebrow" style={{ color: INK }}>step {s.step}</div>
            <div className="display" style={{ fontSize: "1.2rem", textTransform: "uppercase", marginTop: 2 }}>{s.label}</div>
            <div className="font-mono" style={{ fontSize: "0.62rem", color: "var(--ink-soft)", wordBreak: "break-all", marginTop: 3 }}>
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

/** One ad creative as a framed tile: the real image when we have it (served by
 * the /api/media proxy), otherwise the type glyph + ad copy. Falls back to the
 * glyph if the image fails to load, so a missing proxy never shows a broken icon. */
function CreativeTile({ c }: { c: GemDossier["creatives"][number] }) {
  const [broken, setBroken] = useState(false);
  const showImage = c.type === "image" && !!c.snapshot_image_url && !broken;
  return (
    <Polaroid caption={c.title} width={158}>
      {showImage ? (
        // eslint-disable-next-line @next/next/no-img-element -- dynamic /api/media proxy thumbnails; next/image optimizer is a poor fit for an internal tool
        <img
          src={c.snapshot_image_url}
          alt={c.title ?? c.body_text ?? "ad creative"}
          loading="lazy"
          onError={() => setBroken(true)}
          style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
        />
      ) : (
        <div style={{ textAlign: "center", padding: 12 }}>
          <div className="display" style={{ fontSize: "1.8rem" }}>{c.type === "video" ? "▶" : "▣"}</div>
          <div className="font-mono" style={{ fontSize: "0.56rem", color: "var(--ink-soft)", marginTop: 8 }}>{c.body_text}</div>
        </div>
      )}
    </Polaroid>
  );
}

function MarginNote({ label, value }: { label: string; value: React.ReactNode }) {
  if (value === undefined || value === null || value === "") return null;
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, padding: "7px 0", borderBottom: "1px solid var(--hair)" }}>
      <span className="eyebrow" style={{ color: "var(--muted)" }}>{label}</span>
      <span className="font-mono" style={{ fontSize: "0.74rem", textAlign: "right" }}>{value}</span>
    </div>
  );
}

export function Dossier({ advertiserId }: { advertiserId: number }) {
  const [gem, setGem] = useState<GemDossier | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let alive = true;
    getGem(advertiserId)
      .then((g) => {
        if (!alive) return;
        setGem(g);
        setError(false);
        setLoading(false);
      })
      .catch(() => {
        if (alive) {
          setError(true);
          setLoading(false);
        }
      });
    return () => {
      alive = false;
    };
  }, [advertiserId, reload]);

  if (error) {
    return <Fallback message={`grug can not open file #${advertiserId}. server sleeping?`} onRetry={() => { setError(false); setLoading(true); setReload((n) => n + 1); }} />;
  }
  if (loading) {
    return <div className="display" style={{ padding: 60, fontSize: "1.6rem", textTransform: "uppercase", color: "var(--muted)" }}>grug open file…</div>;
  }
  if (!gem) {
    return (
      <div style={{ padding: 60 }}>
        <p className="display" style={{ fontSize: "1.6rem", textTransform: "uppercase" }}>no file for #{advertiserId}.</p>
        <Link href="/board" className="font-mono uline" style={{ color: "var(--ink)", display: "inline-flex", alignItems: "center", gap: 5 }}><IconArrowLeft size={14} stroke={2} /> back to board</Link>
      </div>
    );
  }

  const chart: ChartPoint[] = gem.scaling.map((s) => ({ label: fmtWeek(s.week_start), value: s.active_ad_count }));
  const status = gem.alert ? { label: "getting big", tone: "red" as const } : { label: "real · 14 day+", tone: "ink" as const };

  return (
    <div style={{ maxWidth: 1100, margin: "0 auto", padding: "20px 22px 80px" }}>
      <Link href="/board" className="font-mono uline" style={{ color: "var(--ink)", fontSize: "0.74rem", display: "inline-flex", alignItems: "center", gap: 5 }}><IconArrowLeft size={14} stroke={2} /> back to board</Link>

      {/* File header */}
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 24, flexWrap: "wrap", marginTop: 20, borderBottom: `1.5px solid ${INK}`, paddingBottom: 22 }}>
        <div style={{ flex: "1 1 360px" }}>
          <div className="eyebrow" style={{ color: INK }}>
            file · {gem.cohort}{gem.app_id === null && " · web only"}
          </div>
          <h1 className="display-x" style={{ fontSize: "clamp(2.6rem, 8vw, 5.5rem)", margin: "6px 0 0" }}>
            {gem.app_name ?? gem.page_name}
          </h1>
          <p style={{ fontSize: "clamp(1.05rem, 2.4vw, 1.5rem)", marginTop: 10, fontWeight: 500, maxWidth: 640 }}>{gem.tagline}</p>
          <div style={{ marginTop: 14 }}><Stamp label={status.label} tone={status.tone} /></div>
        </div>
        <div style={{ textAlign: "right", flexShrink: 0 }}>
          <div className="display-x" style={{ fontSize: "clamp(4rem, 14vw, 8rem)" }}>{Math.round(gem.gem_score)}</div>
          <div className="eyebrow">Gemscore</div>
        </div>
      </div>

      {/* Two-column body */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 32, marginTop: 30 }}>
        <div>
          <SectionTitle>how fast it grow — ads per week</SectionTitle>
          <SketchLineChart points={chart} height={190} highlightLast={gem.alert} />

          <div style={{ marginTop: 30 }}>
            <SectionTitle>the ads</SectionTitle>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 14 }}>
              {gem.creatives.map((c) => (
                <CreativeTile key={c.id} c={c} />
              ))}
            </div>
          </div>
        </div>

        <div>
          <SectionTitle>where click go</SectionTitle>
          <FunnelChain steps={gem.funnel} />

          <div style={{ marginTop: 30 }}>
            <SectionTitle>who make it</SectionTitle>
            <div style={{ border: `1.5px solid ${INK}`, background: "var(--paper)", padding: "14px 18px" }}>
              <MarginNote label="site" value={gem.company.domain} />
              <MarginNote label="site age" value={gem.company.domain_age_years ? `${gem.company.domain_age_years} yrs` : undefined} />
              <MarginNote label="owner" value={gem.company.parent_company ?? "nobody. just them"} />
              <MarginNote label="kind" value={gem.company.page_category} />
              <MarginNote label="instagram" value={gem.company.ig_username ? `@${gem.company.ig_username}` : undefined} />
              <MarginNote label="ig follower" value={gem.company.ig_followers?.toLocaleString()} />
              <MarginNote label="price" value={gem.pricing} />
              {gem.company.tech && gem.company.tech.length > 0 && (
                <div style={{ marginTop: 12, display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {gem.company.tech.map((t) => (
                    <span key={t} className="font-mono" style={{ fontSize: "0.6rem", padding: "3px 8px", border: "1.2px solid var(--ink)", textTransform: "uppercase" }}>{t}</span>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
