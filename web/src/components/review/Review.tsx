"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AnimatePresence, motion } from "framer-motion";
import { getReviewQueue, postReviewDecision } from "@/lib/api";
import { Stamp } from "@/components/sketch";
import { Fallback } from "@/components/Fallback";
import { INK } from "@/components/sketch/palette";
import { IconCheck, IconHelp, IconX } from "@tabler/icons-react";
import type { ReviewQueueRow, Verdict } from "@/types/contracts";

const VERDICTS: { verdict: Verdict; label: string; inverted: boolean; Icon: typeof IconCheck }[] = [
  { verdict: "accept", label: "yes", inverted: true, Icon: IconCheck },
  { verdict: "hold", label: "maybe", inverted: false, Icon: IconHelp },
  { verdict: "reject", label: "no", inverted: false, Icon: IconX },
];

const STAMP_LABEL: Record<Verdict, { label: string; tone: "ink" | "red" }> = {
  accept: { label: "yes. good", tone: "red" },
  hold: { label: "maybe", tone: "ink" },
  reject: { label: "no", tone: "ink" },
};

function QueueSlip({ row, onDecide }: { row: ReviewQueueRow; onDecide: (id: number, v: Verdict) => void }) {
  const [stamped, setStamped] = useState<Verdict | null>(null);

  const decide = (verdict: Verdict) => {
    if (stamped) return;
    setStamped(verdict);
    setTimeout(() => onDecide(row.advertiser_id, verdict), 700);
  };

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.95, transition: { duration: 0.3 } }}
      style={{ position: "relative", border: `1.5px solid ${INK}`, background: "var(--paper)" }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 18, padding: "16px 20px", flexWrap: "wrap" }}>
        <div style={{ textAlign: "center", minWidth: 64 }}>
          <div className="display-x" style={{ fontSize: "2.6rem" }}>{Math.round(row.gem_score)}</div>
          <div className="eyebrow">Score</div>
        </div>

        <div style={{ flex: "1 1 220px" }}>
          <Link href={`/gems/${row.advertiser_id}`} style={{ textDecoration: "none", color: "var(--ink)" }}>
            <div className="display" style={{ fontSize: "1.5rem", textTransform: "uppercase" }}>{row.app_name ?? row.page_name}</div>
          </Link>
          <div className="font-mono" style={{ fontSize: "0.64rem", color: "var(--muted)", marginTop: 4, textTransform: "uppercase", letterSpacing: "0.04em" }}>
            {row.cohort} · {row.active_ad_count} ads now · {row.max_longevity_days}d up
          </div>
        </div>

        <div style={{ display: "flex", gap: 10 }}>
          {VERDICTS.map((v) => (
            <button
              key={v.verdict}
              onClick={() => decide(v.verdict)}
              className="display"
              style={{
                cursor: "pointer",
                background: v.inverted ? INK : "transparent",
                color: v.inverted ? "var(--paper)" : INK,
                border: `1.5px solid ${INK}`,
                padding: "8px 16px",
                fontSize: "0.95rem",
                textTransform: "uppercase",
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              <v.Icon size={16} stroke={2} />
              {v.label}
            </button>
          ))}
        </div>
      </div>

      <AnimatePresence>
        {stamped && (
          <motion.div
            initial={{ opacity: 0, scale: 1.8 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ type: "spring", stiffness: 320, damping: 16 }}
            style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", pointerEvents: "none", background: "rgba(255,255,255,0.6)" }}
          >
            <div style={{ transform: "scale(1.6)" }}>
              <Stamp label={STAMP_LABEL[stamped].label} tone={STAMP_LABEL[stamped].tone} />
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

export function Review() {
  const [rows, setRows] = useState<ReviewQueueRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let alive = true;
    getReviewQueue()
      .then((r) => {
        if (!alive) return;
        setRows(r);
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
  }, [reload]);

  const handleDecide = async (id: number, verdict: Verdict) => {
    try {
      await postReviewDecision(id, { verdict, reviewer: "founder" });
      setRows((prev) => prev.filter((r) => r.advertiser_id !== id));
    } catch {
      // write failed (live API down): surface the error; retry refetches a clean queue
      setError(true);
    }
  };

  return (
    <div style={{ maxWidth: 800, margin: "0 auto", padding: "26px 22px 80px" }}>
      <h1 className="display-x" style={{ fontSize: "clamp(2.4rem, 7vw, 4rem)", margin: 0 }}>the pile</h1>
      <p className="font-mono" style={{ fontSize: "0.74rem", color: "var(--muted)", marginTop: 8, textTransform: "uppercase", letterSpacing: "0.06em" }}>
        gem score 70 up. grug not decide. you stamp.
      </p>

      <div style={{ display: "flex", flexDirection: "column", gap: 16, marginTop: 26 }}>
        {error ? (
          <Fallback message="grug can not reach pile. server sleeping?" onRetry={() => { setError(false); setLoading(true); setReload((n) => n + 1); }} />
        ) : loading ? (
          <div className="display" style={{ fontSize: "1.5rem", textTransform: "uppercase", color: "var(--muted)" }}>grug get pile…</div>
        ) : rows.length === 0 ? (
          <div style={{ border: `1.5px solid ${INK}`, padding: "28px 20px", textAlign: "center" }}>
            <div className="display-x" style={{ fontSize: "1.8rem" }}>pile empty</div>
            <p className="font-mono" style={{ fontSize: "0.72rem", color: "var(--muted)", marginTop: 8 }}>
              every gem get stamp. grug rest now.{" "}
              <Link href="/board" className="uline" style={{ color: "var(--ink)" }}>back to board</Link>
            </p>
          </div>
        ) : (
          <AnimatePresence mode="popLayout">
            {rows.map((row) => (
              <QueueSlip key={row.advertiser_id} row={row} onDecide={handleDecide} />
            ))}
          </AnimatePresence>
        )}
      </div>
    </div>
  );
}
