"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { IconArrowLeft } from "@tabler/icons-react";
import { getGem } from "@/lib/api";
import { markVisitedGem } from "@/lib/onboarding";
import { loadReturnTo } from "@/lib/returnTo";
import { Fallback } from "@/components/Fallback";
import { ButtonLink } from "@/components/ui";
import type { GemDossier } from "@/types/contracts";
import { GemHeader } from "./GemHeader";
import { AdGrowth, AdsSection } from "./EvidenceReel";
import { AppStorePreview } from "./AppStorePreview";
import { GemFacts } from "./GemFacts";
import { GemNotes } from "./GemNotes";

// The stored return URL never changes while this page is mounted.
const subscribeNever = () => () => {};

const backLink: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 5,
  color: "var(--muted)",
  textDecoration: "none",
  fontSize: "var(--fs-xs)",
};

/**
 * The dossier page, top to bottom: header (identity + score + store facts +
 * links), the ad-growth curve beside notes/details, the collected ads, then
 * the store-listing deep dive.
 */
export function GemDetail({ advertiserId }: { advertiserId: number }) {
  const [gem, setGem] = useState<GemDossier | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [reload, setReload] = useState(0);
  // The exact Discover URL (page + filters) the user came from, if any.
  // sessionStorage is client-only, so the server snapshot is the plain
  // /discover and the stored URL takes over right after hydration.
  const backHref = useSyncExternalStore(
    subscribeNever,
    () => loadReturnTo(window.sessionStorage)?.url ?? "/discover",
    () => "/discover",
  );

  useEffect(() => {
    markVisitedGem(); // activation: "Open a gem"
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
    return <Fallback message={`Gem #${advertiserId} didn't load. Check that the server is running, then try again.`} onRetry={() => { setError(false); setLoading(true); setReload((n) => n + 1); }} />;
  }
  if (loading) {
    return <div className="t-lg w-strong" style={{ padding: 48, color: "var(--muted)" }}>Loading…</div>;
  }
  if (!gem) {
    return (
      <main style={{ maxWidth: 480, margin: "56px auto", textAlign: "center", display: "flex", flexDirection: "column", alignItems: "center", gap: 16, padding: "0 22px" }}>
        <p className="t-lg w-strong">No gem with id #{advertiserId}.</p>
        <ButtonLink href="/discover">Back to Discover</ButtonLink>
      </main>
    );
  }

  return (
    <main style={{ maxWidth: 1140, margin: "0 auto", padding: "18px 22px 80px" }}>
      {/* scroll={false}: DiscoverFeed restores the saved offset itself; Next's
          default scroll-to-top races it and sometimes lands second. */}
      <Link href={backHref} scroll={false} className="font-mono" style={backLink}>
        <IconArrowLeft size={14} stroke={2} /> Back to Discover
      </Link>

      <div style={{ marginTop: 14 }}>
        <GemHeader gem={gem} />
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 32, marginTop: 32, alignItems: "flex-start" }}>
        <div style={{ flex: "1.7 1 420px", minWidth: 0 }}>
          <AdGrowth gem={gem} />
        </div>
        <aside style={{ flex: "1 1 280px", display: "flex", flexDirection: "column", gap: 28 }}>
          <GemNotes row={gem} />
          <GemFacts gem={gem} />
        </aside>
      </div>

      <div style={{ marginTop: 36 }}>
        <AdsSection gem={gem} />
      </div>

      <div style={{ marginTop: 36 }}>
        <AppStorePreview gem={gem} />
      </div>
    </main>
  );
}
