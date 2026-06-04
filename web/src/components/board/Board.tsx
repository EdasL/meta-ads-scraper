"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { motion, useMotionValue } from "framer-motion";
import { getShortlist } from "@/lib/api";
import { GemCard } from "@/components/sketch";
import { seedFrom } from "@/components/sketch/palette";
import { Marquee, LiveClock, CornerHud, SessionTree } from "@/components/chrome";
import type { ShortlistRow } from "@/types/contracts";
import { BoardToolbar } from "./BoardToolbar";

type Pos = { x: number; y: number };
const LAYOUT_KEY = "fielddesk.board";
const CARD_W = 256;

function defaultPos(gem: ShortlistRow, index: number): Pos {
  const col = index % 3;
  const row = Math.floor(index / 3);
  const jitter = (seedFrom(`${gem.advertiser_id}`) % 40) - 20;
  return { x: 56 + col * 300 + jitter, y: 76 + row * 340 + (jitter / 2) };
}

function loadLayout(): Record<number, Pos> {
  if (typeof window === "undefined") return {};
  try {
    return JSON.parse(window.localStorage.getItem(LAYOUT_KEY) ?? "{}");
  } catch {
    return {};
  }
}

function DraggableCard({
  gem,
  pos,
  onMove,
  onOpen,
}: {
  gem: ShortlistRow;
  pos: Pos;
  onMove: (id: number, p: Pos) => void;
  onOpen: (id: number) => void;
}) {
  const x = useMotionValue(pos.x);
  const y = useMotionValue(pos.y);
  const moved = useRef(false);

  return (
    <motion.div
      drag
      dragMomentum={false}
      style={{ position: "absolute", left: 0, top: 0, x, y, width: CARD_W, cursor: "grab", touchAction: "none" }}
      whileDrag={{ cursor: "grabbing", scale: 1.03, zIndex: 20 }}
      onDragStart={() => {
        moved.current = false;
      }}
      onDrag={(_e, info) => {
        if (Math.abs(info.offset.x) + Math.abs(info.offset.y) > 5) moved.current = true;
      }}
      onDragEnd={() => onMove(gem.advertiser_id, { x: x.get(), y: y.get() })}
      onClick={() => {
        if (!moved.current) onOpen(gem.advertiser_id);
      }}
    >
      <GemCard gem={gem} width={CARD_W} />
    </motion.div>
  );
}

export function Board() {
  const router = useRouter();
  const [rows, setRows] = useState<ShortlistRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [positions, setPositions] = useState<Record<number, Pos>>({});
  const [cohort, setCohort] = useState("");
  const [minScore, setMinScore] = useState(0);

  useEffect(() => {
    getShortlist().then((r) => {
      setRows(r);
      const saved = loadLayout();
      const next: Record<number, Pos> = {};
      r.forEach((gem, i) => {
        next[gem.advertiser_id] = saved[gem.advertiser_id] ?? defaultPos(gem, i);
      });
      setPositions(next);
      setLoading(false);
    });
  }, []);

  const cohorts = useMemo(() => Array.from(new Set(rows.map((r) => r.cohort))), [rows]);

  const visible = useMemo(
    () => rows.filter((r) => (!cohort || r.cohort === cohort) && r.gem_score >= minScore),
    [rows, cohort, minScore],
  );

  const persist = (next: Record<number, Pos>) => {
    setPositions(next);
    if (typeof window !== "undefined") window.localStorage.setItem(LAYOUT_KEY, JSON.stringify(next));
  };

  const handleMove = (id: number, p: Pos) => persist({ ...positions, [id]: p });

  const resetLayout = () => {
    const next: Record<number, Pos> = {};
    rows.forEach((gem, i) => (next[gem.advertiser_id] = defaultPos(gem, i)));
    persist(next);
  };

  const canvasH = Math.max(900, Math.ceil(rows.length / 3) * 340 + 120);

  const ticker = [
    "getting big",
    "still hidden",
    "14 day proof",
    "no guess",
    "grug watch meta ads",
    ...cohorts.map((c) => c.toLowerCase()),
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 53px)" }}>
      <BoardToolbar
        cohorts={cohorts}
        cohort={cohort}
        minScore={minScore}
        count={visible.length}
        onCohort={setCohort}
        onMinScore={setMinScore}
        onReset={resetLayout}
      />

      <Marquee items={ticker} />

      <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
        <SessionTree
          rows={rows}
          activeCohort={cohort}
          onPickCohort={setCohort}
          onOpenGem={(id) => router.push(`/gems/${id}`)}
        />

        <div style={{ position: "relative", flex: 1, minWidth: 0 }}>
          <CornerHud
            topLeft={<>FIELD DESK / REV A — BUILD 003<br />SESSION: GEM_HUNT_01 / TAKE 07</>}
            topRight={<LiveClock />}
            bottomLeft={<>META ADS LIBRARY / EU + US FEED<br />MODE: MOCK / NOT FOR DISTRIBUTION</>}
            bottomRight={<>grab card. drag. find out.<br />{visible.length} gem on board</>}
          />

          <div
            style={{
              position: "absolute",
              inset: 0,
              overflow: "auto",
              background: "transparent",
            }}
          >
            {loading ? (
              <div className="display" style={{ padding: 60, fontSize: "1.6rem", textTransform: "uppercase", color: "var(--muted)" }}>
                grug get board…
              </div>
            ) : (
              <div style={{ position: "relative", width: 1100, height: canvasH, minWidth: "100%" }}>
                {visible.map((gem) => (
                  <DraggableCard
                    key={gem.advertiser_id}
                    gem={gem}
                    pos={positions[gem.advertiser_id] ?? { x: 40, y: 40 }}
                    onMove={handleMove}
                    onOpen={(id) => router.push(`/gems/${id}`)}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
