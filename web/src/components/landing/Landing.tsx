"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { GEMS } from "@/lib/mock/fixtures";
import { Stamp, SketchLineChart, type ChartPoint } from "@/components/sketch";
import { Marquee, CornerHud, LiveClock } from "@/components/chrome";
import type { GemDossier } from "@/types/contracts";

const IDEAS = [...GEMS].sort((a, b) => b.gem_score - a.gem_score).slice(0, 3);

function fmtWeek(iso: string): string {
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

function chartData(gem: GemDossier): ChartPoint[] {
  return gem.scaling.map((s) => ({ label: fmtWeek(s.week_start), value: s.active_ad_count }));
}

function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span
      className="font-mono"
      style={{
        fontSize: "0.7rem",
        padding: "5px 12px",
        border: "1.5px solid var(--ink)",
        textTransform: "uppercase",
        letterSpacing: "0.04em",
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </span>
  );
}

function IdeaPanel({ gem, index }: { gem: GemDossier; index: number }) {
  return (
    <section
      style={{
        minHeight: "100vh",
        scrollSnapAlign: "start",
        display: "flex",
        alignItems: "center",
        padding: "clamp(24px, 6vw, 80px)",
        position: "relative",
        borderTop: "1.5px solid var(--ink)",
      }}
    >
      <div style={{ width: "100%", maxWidth: 1100, margin: "0 auto" }}>
        <div className="eyebrow" style={{ display: "flex", justifyContent: "space-between" }}>
          <span>idea that work</span>
          <span>{String(index + 1).padStart(2, "0")} / {String(IDEAS.length).padStart(2, "0")}</span>
        </div>

        <motion.div
          initial={{ opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ amount: 0.4 }}
          transition={{ duration: 0.5, ease: "easeOut" }}
          style={{ marginTop: 18 }}
        >
          <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 24, flexWrap: "wrap" }}>
            <h2 className="display-x" style={{ fontSize: "clamp(3rem, 11vw, 8rem)", margin: 0 }}>
              {gem.app_name ?? gem.page_name}
            </h2>
            <div style={{ textAlign: "right" }}>
              <div className="display-x" style={{ fontSize: "clamp(3.5rem, 12vw, 9rem)" }}>{Math.round(gem.gem_score)}</div>
              <div className="eyebrow">Gemscore</div>
            </div>
          </div>

          <p style={{ fontSize: "clamp(1.15rem, 3vw, 2rem)", lineHeight: 1.15, margin: "10px 0 0", maxWidth: 760, fontWeight: 500 }}>
            {gem.tagline}
          </p>

          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 24 }}>
            <Stamp label="proven. it work." tone="red" />
            <Chip>{gem.active_ad_count} ads now</Chip>
            <Chip>{gem.max_longevity_days} day up</Chip>
            {gem.pricing && <Chip>{gem.pricing}</Chip>}
          </div>

          <div style={{ marginTop: 28, borderTop: "1.5px solid var(--ink)", paddingTop: 16 }}>
            <div className="eyebrow" style={{ marginBottom: 8 }}>ads per week — line go up, app get big</div>
            <SketchLineChart points={chartData(gem)} height={150} highlightLast />
          </div>
        </motion.div>
      </div>
    </section>
  );
}

export function Landing() {
  return (
    <main
      style={{
        height: "100vh",
        overflowY: "scroll",
        scrollSnapType: "y mandatory",
        background: "transparent",
      }}
    >
      {/* Hero */}
      <section
        style={{
          minHeight: "100vh",
          scrollSnapAlign: "start",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: "clamp(24px, 6vw, 80px)",
          position: "relative",
        }}
      >
        <div style={{ position: "absolute", top: 24, left: "clamp(24px,6vw,80px)", right: "clamp(24px,6vw,80px)", display: "flex", justifyContent: "space-between" }}>
          <span className="eyebrow" style={{ color: "var(--ink)" }}>(field desk)</span>
          <Link href="/board" className="eyebrow uline" style={{ color: "var(--ink)", textDecoration: "none" }}>
            go to board →
          </Link>
        </div>

        <motion.h1
          initial={{ opacity: 0, y: 24 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6 }}
          className="display-x"
          style={{ fontSize: "clamp(3rem, 13vw, 11rem)", margin: 0, maxWidth: 1300 }}
        >
          find idea that <span className="blackout">already work</span>
        </motion.h1>

        <motion.p
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.4, duration: 0.6 }}
          style={{ fontSize: "clamp(1rem, 2.4vw, 1.5rem)", maxWidth: 660, marginTop: 28, lineHeight: 1.3, fontWeight: 500 }}
        >
          grug watch meta ads all day. grug see small app get big, quiet. grug
          catch it at 14 day, before everyone copy. you build. no guess.
        </motion.p>

        <CornerHud
          inset={24}
          bottomLeft={<>33.94° N 118.40° W / meta ads library<br />rev a — build 003 / not for distribution</>}
          bottomRight={<LiveClock />}
        />

        <motion.div
          animate={{ y: [0, 9, 0] }}
          transition={{ repeat: Infinity, duration: 1.6, ease: "easeInOut" }}
          style={{ position: "absolute", bottom: 30, left: "50%", transform: "translateX(-50%)", textAlign: "center" }}
        >
          <div className="eyebrow" style={{ color: "var(--ink)", marginBottom: 4 }}>start here</div>
          <div className="display" style={{ fontSize: "1.6rem" }}>↓</div>
        </motion.div>
      </section>

      <Marquee items={["proven not guess", "small app", "get big quiet", "caught at 14 day", "before everyone copy", "grug point — you build"]} />


      {IDEAS.map((gem, i) => (
        <IdeaPanel key={gem.advertiser_id} gem={gem} index={i} />
      ))}

      {/* CTA */}
      <section
        style={{
          minHeight: "100vh",
          scrollSnapAlign: "start",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: "clamp(24px, 6vw, 80px)",
          borderTop: "1.5px solid var(--ink)",
          background: "var(--ink)",
          color: "var(--paper)",
        }}
      >
        <motion.div initial={{ opacity: 0, y: 24 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ amount: 0.5 }} transition={{ duration: 0.5 }}>
          <h2 className="display-x" style={{ fontSize: "clamp(2.6rem, 10vw, 8rem)", margin: 0, maxWidth: 1100 }}>
            many more on board
          </h2>
          <p style={{ fontSize: "clamp(1rem, 2.2vw, 1.4rem)", marginTop: 18, fontWeight: 500, opacity: 0.8 }}>
            pin good one. open file. stamp winner.
          </p>
          <div style={{ display: "flex", gap: 14, marginTop: 36, flexWrap: "wrap" }}>
            <Link
              href="/board"
              className="display"
              style={{ background: "var(--paper)", color: "var(--ink)", padding: "16px 32px", fontSize: "1.3rem", textTransform: "uppercase", textDecoration: "none" }}
            >
              see whole board →
            </Link>
            <Link
              href="/review"
              className="display"
              style={{ border: "1.5px solid var(--paper)", color: "var(--paper)", padding: "16px 32px", fontSize: "1.3rem", textTransform: "uppercase", textDecoration: "none" }}
            >
              the pile
            </Link>
          </div>
        </motion.div>
      </section>
    </main>
  );
}
