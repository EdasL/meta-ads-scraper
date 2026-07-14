"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { IconPlus } from "@tabler/icons-react";
import { Button } from "@/components/ui";
import { huntIsActive } from "@/lib/hunt";
import {
  getHunt,
  getHunts,
  postHunt,
  postHuntMessage,
  postHuntRound,
  type HuntMessage,
  type HuntRound,
  type HuntSettings,
  type HuntSummary,
  type HuntThread,
} from "@/lib/api";
import { ProposalCard } from "./ProposalCard";
import { RoundBlock } from "./RoundBlock";

/** Colour for a hunt's last-round status dot in the left rail. */
function statusDot(status: string | null): string {
  if (status === "done") return "var(--up)";
  if (status === "running" || status === "queued") return "var(--warn)";
  if (status === "error") return "var(--down)";
  return "var(--faint)";
}

function fmtDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function HuntView() {
  const [hunts, setHunts] = useState<HuntSummary[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [thread, setThread] = useState<HuntThread | null>(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  // The just-sent message, rendered as a user bubble immediately (before the
  // server round-trip finishes) so sending feels instant.
  const [pendingText, setPendingText] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const scroller = useRef<HTMLDivElement | null>(null);

  const refreshHunts = useCallback(async () => {
    try {
      setHunts(await getHunts());
    } catch {
      /* transient — the rail just stays as-is */
    }
  }, []);

  const loadThread = useCallback(async (id: number) => {
    try {
      setThread(await getHunt(id));
    } catch {
      /* transient — keep the last good thread */
    }
  }, []);

  // Initial load: hunt list, and the most recent thread if there is one.
  useEffect(() => {
    let cancelled = false;
    getHunts()
      .then((list) => {
        if (cancelled) return;
        setHunts(list);
        if (list.length > 0) {
          setActiveId(list[0].id);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  // Load a thread whenever a hunt is selected. Clearing back to the empty
  // "new hunt" state is done by newHunt() (which nulls both), so the effect
  // only ever loads. The fetch resolves into setState inside a `.then` (not the
  // synchronous effect body) to match the rest of the app's data-load pattern.
  useEffect(() => {
    if (activeId == null) return;
    let cancelled = false;
    getHunt(activeId)
      .then((t) => {
        if (!cancelled) setThread(t);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [activeId]);

  // Live polling — cadence derived from the current thread, re-evaluated after
  // every refresh (mirrors RefreshDataButton: a single interval in a ref, resumed
  // on mount, stopped when idle). While a round is queued|running: 2s. Idle but a
  // kept result still lacks a GemScore: 30s. Otherwise: no polling.
  useEffect(() => {
    if (timer.current) {
      clearInterval(timer.current);
      timer.current = null;
    }
    if (!thread) return;
    const id = thread.hunt.id;
    const active = huntIsActive(thread.rounds);
    const awaitingScores =
      !active && thread.results.some((r) => r.triage === "kept" && r.gem_score == null);
    const period = active ? 2000 : awaitingScores ? 30000 : 0;
    if (period === 0) return;
    timer.current = setInterval(() => {
      loadThread(id);
    }, period);
    return () => {
      if (timer.current) {
        clearInterval(timer.current);
        timer.current = null;
      }
    };
  }, [thread, loadThread]);

  // The message that should carry the editable proposal: the latest assistant
  // message with a proposal and no round started from it yet.
  const proposalMsgId = useMemo(() => {
    if (!thread) return null;
    const started = new Set(thread.rounds.map((r) => r.message_id).filter((m): m is number => m != null));
    let id: number | null = null;
    for (const m of thread.messages) {
      if (m.role === "assistant" && m.proposal && !started.has(m.id)) id = m.id;
    }
    return id;
  }, [thread]);

  const roundsByMsg = useMemo(() => {
    const map = new Map<number, HuntRound[]>();
    const orphans: HuntRound[] = [];
    for (const r of thread?.rounds ?? []) {
      if (r.message_id == null) orphans.push(r);
      else {
        const list = map.get(r.message_id) ?? [];
        list.push(r);
        map.set(r.message_id, list);
      }
    }
    return { map, orphans };
  }, [thread]);

  const roundActive = thread ? huntIsActive(thread.rounds) : false;

  const resultsFor = useCallback(
    (roundId: number) => (thread?.results ?? []).filter((r) => r.round_id === roundId),
    [thread],
  );

  // Optimistic render: drop a fresh round into local state right away so
  // RoundBlock shows it (queued) and polling starts, without waiting for the
  // loadThread round-trip. Guard on hunt.id so a stale in-flight response can't
  // land on another thread, and dedupe by id so a later loadThread that already
  // carries the round doesn't double it. Used by both the founder-approved
  // Start hunt and a Peon-started round returned on a chat turn.
  const mergeRound = useCallback((huntId: number, round: HuntRound) => {
    setThread((prev) =>
      prev && prev.hunt.id === huntId && !prev.rounds.some((r) => r.id === round.id)
        ? { ...prev, rounds: [...prev.rounds, round] }
        : prev,
    );
  }, []);

  const startRound = useCallback(
    async (messageId: number, terms: string[], competitorApps: string[], settings?: HuntSettings) => {
      if (activeId == null) return;
      const round = await postHuntRound(activeId, {
        message_id: messageId,
        terms,
        competitor_apps: competitorApps,
        settings,
      });
      mergeRound(activeId, round);
      await loadThread(activeId);
      refreshHunts();
    },
    [activeId, loadThread, refreshHunts, mergeRound],
  );

  const send = useCallback(async () => {
    const message = draft.trim();
    if (!message || sending) return;
    setSending(true);
    setDraft("");
    setPendingText(message); // instant user bubble; the reload replaces it
    try {
      if (activeId == null) {
        const turn = await postHunt(message);
        setActiveId(turn.hunt_id);
        await loadThread(turn.hunt_id);
        // A first turn can start a round; loadThread already fetched it, so this
        // dedupes to a no-op — kept for parity with the existing-thread path.
        if (turn.round) mergeRound(turn.hunt_id, turn.round);
        refreshHunts();
      } else {
        const turn = await postHuntMessage(activeId, message);
        // Peon may start a round on this turn — merge it so polling begins now,
        // before the loadThread round-trip below reconciles the full thread.
        if (turn.round) mergeRound(activeId, turn.round);
        await loadThread(activeId);
      }
    } catch {
      // Restore the draft so the founder can retry.
      setDraft(message);
    } finally {
      setPendingText(null);
      setSending(false);
    }
  }, [draft, sending, activeId, loadThread, refreshHunts, mergeRound]);

  // Keep the transcript pinned to the newest message: jump on thread switch and
  // whenever a message lands or a send starts. Round polling refreshes don't
  // change the message count, so they never yank the founder's scroll position.
  const messageCount = thread?.messages.length ?? 0;
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [activeId, messageCount, pendingText, sending]);

  function onComposerKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  function newHunt() {
    setActiveId(null);
    setThread(null);
    setDraft("");
  }

  return (
    <main style={{ maxWidth: 1100, margin: "0 auto", padding: "24px 22px 80px", display: "flex", gap: 24, alignItems: "flex-start", flexWrap: "wrap" }}>
      {/* Left rail — hunt history */}
      <aside style={{ flex: "0 0 240px", minWidth: 220, display: "flex", flexDirection: "column", gap: 12 }}>
        <Button variant="ghost" onClick={newHunt} style={{ justifyContent: "flex-start" }}>
          <IconPlus size={14} stroke={1.75} />
          New hunt
        </Button>
        <div style={{ display: "flex", flexDirection: "column" }}>
          {hunts.map((h) => {
            const active = h.id === activeId;
            return (
              <button
                key={h.id}
                type="button"
                onClick={() => setActiveId(h.id)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  textAlign: "left",
                  width: "100%",
                  padding: "10px 10px",
                  border: "none",
                  borderLeft: active ? "2px solid var(--ink)" : "2px solid transparent",
                  background: active ? "var(--surface-3)" : "transparent",
                  cursor: "pointer",
                }}
              >
                <span aria-hidden style={{ width: 7, height: 7, borderRadius: "50%", background: statusDot(h.last_round_status), flex: "0 0 auto" }} />
                <span style={{ flex: "1 1 auto", minWidth: 0 }}>
                  <span className="t-sm w-strong" style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--ink)" }}>
                    {h.title || "Untitled hunt"}
                  </span>
                  <span className="t-xs nums" style={{ color: "var(--muted)" }}>
                    {fmtDate(h.created_at)}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      </aside>

      {/* Thread + composer: the transcript is its own scroll container and the
          composer sits BELOW it — messages can never slide under the input. */}
      <section
        style={{
          flex: "1 1 480px",
          minWidth: 300,
          display: "flex",
          flexDirection: "column",
          gap: 12,
          height: "calc(100vh - 170px)",
          minHeight: 420,
        }}
      >
        <div style={{ flex: "0 0 auto" }}>
          <h1 className="t-2xl w-heavy" style={{ margin: 0 }}>
            Hunt
          </h1>
          <p className="t-sm" style={{ color: "var(--muted)", margin: "4px 0 0" }}>
            Describe a niche or a competitor app. Peon proposes search terms; you approve a round.
          </p>
        </div>

        <div
          ref={scroller}
          style={{
            flex: "1 1 auto",
            minHeight: 0,
            overflowY: "auto",
            display: "flex",
            flexDirection: "column",
            gap: 18,
            paddingRight: 6,
            paddingBottom: 8,
          }}
        >
          {(thread?.messages ?? []).map((m) => (
            <div key={m.id}>
              <MessageBubble message={m} />
              {m.id === proposalMsgId && m.proposal && (
                <ProposalCard
                  proposal={m.proposal}
                  roundActive={roundActive}
                  onStart={(terms, competitors) => startRound(m.id, terms, competitors, m.proposal?.settings)}
                />
              )}
              {(roundsByMsg.map.get(m.id) ?? []).map((r) => (
                <RoundBlock key={r.id} round={r} results={resultsFor(r.id)} />
              ))}
            </div>
          ))}
          {roundsByMsg.orphans.map((r) => (
            <RoundBlock key={r.id} round={r} results={resultsFor(r.id)} />
          ))}
          {pendingText != null && (
            <MessageBubble message={{ id: -1, role: "user", content: pendingText, proposal: null, tool_log: null, created_at: "" }} />
          )}
          {sending && <PeonWorking />}
        </div>

        <div style={{ flex: "0 0 auto", display: "flex", flexDirection: "column", gap: 8, borderTop: "1px solid var(--line)", paddingTop: 10 }}>
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onComposerKey}
            rows={2}
            placeholder="Describe a niche or a competitor app"
            className="t-sm"
            style={{
              width: "100%",
              resize: "vertical",
              background: "var(--surface-2)",
              border: "1.5px solid var(--ink)",
              color: "var(--ink)",
              padding: "10px 12px",
              outline: "none",
              fontFamily: "var(--font-sans), system-ui, sans-serif",
            }}
          />
          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <Button onClick={send} disabled={sending || draft.trim() === ""}>
              {sending ? "Sending…" : "Send"}
            </Button>
          </div>
        </div>
      </section>
    </main>
  );
}

/** Peon's in-progress state: a labelled bubble that pulses while the agent turn
 *  (Gemini + tool calls, up to ~30s) runs server-side. */
function PeonWorking() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, alignItems: "flex-start" }}>
      <style>{`@keyframes hunt-working-pulse { 0%, 100% { opacity: 0.35; } 50% { opacity: 1; } }`}</style>
      <span className="eyebrow">Peon</span>
      <div
        className="t-sm"
        style={{
          border: "1.5px solid var(--ink)",
          background: "var(--surface-2)",
          color: "var(--muted)",
          padding: "10px 12px",
          animation: "hunt-working-pulse 1.4s ease-in-out infinite",
        }}
      >
        Peon is working…
      </div>
    </div>
  );
}

/** One transcript entry. Assistant messages are labelled Peon and, when the
 *  turn ran tools, carry a muted mono trace line under the reply. */
function MessageBubble({ message }: { message: HuntMessage }) {
  const isUser = message.role === "user";
  const toolLog = Array.isArray(message.tool_log) ? message.tool_log : [];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, alignItems: isUser ? "flex-end" : "flex-start" }}>
      {!isUser && <span className="eyebrow">Peon</span>}
      <div
        className="t-sm"
        style={{
          maxWidth: "82%",
          whiteSpace: "pre-wrap",
          border: "1.5px solid var(--ink)",
          background: isUser ? "var(--surface-3)" : "var(--surface-2)",
          color: "var(--ink)",
          padding: "10px 12px",
        }}
      >
        {message.content}
      </div>
      {toolLog.length > 0 && (
        <div className="t-xs font-mono" style={{ maxWidth: "82%", color: "var(--muted)" }}>
          {toolLog.join(" · ")}
        </div>
      )}
    </div>
  );
}
