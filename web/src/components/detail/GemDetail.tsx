"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { IconArrowLeft } from "@tabler/icons-react";
import { getGem } from "@/lib/api";
import { markVisitedGem } from "@/lib/onboarding";
import { trendFromScaling } from "@/lib/why";
import { Fallback } from "@/components/Fallback";
import { ButtonLink } from "@/components/ui";
import type { GemDossier } from "@/types/contracts";
import { GemVerdict } from "./GemVerdict";
import { GemLinks } from "./GemLinks";
import { EvidenceReel } from "./EvidenceReel";
import { AppStorePreview, StorePresenceChips } from "./AppStorePreview";
import { GemFacts } from "./GemFacts";
import { GemNotes } from "./GemNotes";

const backLink: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 5,
  color: "var(--muted)",
  textDecoration: "none",
  fontSize: "var(--fs-xs)",
  letterSpacing: "0.08em",
  textTransform: "uppercase",
};

export function GemDetail({ advertiserId }: { advertiserId: number }) {
  const [gem, setGem] = useState<GemDossier | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    markVisitedGem(); // activation: "find a gem"
  }, []);

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
    return <Fallback message={`Can't open file #${advertiserId}. Is the server up?`} onRetry={() => { setError(false); setLoading(true); setReload((n) => n + 1); }} />;
  }
  if (loading) {
    return <div className="t-lg w-strong caps" style={{ padding: 48, color: "var(--muted)" }}>Loading file…</div>;
  }
  if (!gem) {
    return (
      <main style={{ maxWidth: 480, margin: "56px auto", textAlign: "center", display: "flex", flexDirection: "column", alignItems: "center", gap: 16, padding: "0 22px" }}>
        <p className="t-lg w-strong">No file for #{advertiserId}.</p>
        <ButtonLink href="/discover">Back to board</ButtonLink>
      </main>
    );
  }

  const trend = trendFromScaling(gem.scaling);

  return (
    <main style={{ maxWidth: 1100, margin: "0 auto", padding: "18px 22px 80px" }}>
      <Link href="/discover" className="font-mono" style={backLink}>
        <IconArrowLeft size={14} stroke={2} /> Back to board
      </Link>

      <header style={{ margin: "16px 0 20px" }}>
        <h1 className="t-3xl w-heavy caps" style={{ margin: 0, wordBreak: "break-word" }}>{gem.app_name ?? gem.page_name}</h1>
        <div className="eyebrow" style={{ marginTop: 6, display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <span>
            {gem.cohort}
            {gem.tagline ? ` · ${gem.tagline}` : ""}
          </span>
          <StorePresenceChips apps={gem.apps} />
        </div>
      </header>

      <GemVerdict gem={gem} trend={trend} />

      <div style={{ marginTop: 24 }}>
        <GemLinks gem={gem} />
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 32, marginTop: 30, alignItems: "flex-start" }}>
        <div style={{ flex: "1.6 1 360px", minWidth: 0, display: "flex", flexDirection: "column", gap: 30 }}>
          <EvidenceReel gem={gem} />
          <AppStorePreview gem={gem} />
        </div>
        <aside style={{ flex: "1 1 260px", display: "flex", flexDirection: "column", gap: 28 }}>
          <GemNotes row={gem} />
          <GemFacts gem={gem} />
        </aside>
      </div>
    </main>
  );
}
