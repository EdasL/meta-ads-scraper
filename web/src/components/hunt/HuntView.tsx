"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useSession } from "next-auth/react";
import { IconPlus, IconTrash } from "@tabler/icons-react";
import { Button } from "@/components/ui";
import { huntIsActive } from "@/lib/hunt";
import {
  deleteHunt,
  getHunt,
  getHunts,
  postHunt,
  postHuntMessage,
  postHuntRound,
  type HuntCardState,
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

// The last hunt the founder had open, per account, so a reload returns to it
// instead of snapping to the top of the list. Keyed by email so two accounts on
// one browser never restore each other's thread.
function lastHuntKey(email: string | null): string | null {
  return email ? `fd:hunt:last:${email}` : null;
}
function readLastHunt(email: string | null): number | null {
  const k = lastHuntKey(email);
  if (!k || typeof window === "undefined") return null;
  const n = Number(window.localStorage.getItem(k));
  return Number.isInteger(n) && n > 0 ? n : null;
}
function writeLastHunt(email: string | null, id: number): void {
  const k = lastHuntKey(email);
  if (k && typeof window !== "undefined") window.localStorage.setItem(k, String(id));
}

// A round's wrap-up (Peon's reflection, or the daemon's canned fallback) is
// written server-side up to ~90s AFTER the round flips to done — polling would
// otherwise stop the instant it's done and never surface it. True while the
// most-recently-finished round still has no assistant message after it, within a
// bounded window so a thread whose message genuinely never comes stops polling.
const WRAPUP_WAIT_MS = 180_000;
function wrapupPending(thread: HuntThread, nowMs: number): boolean {
  let lastFinishMs = -1;
  for (const r of thread.rounds) {
    if ((r.status === "done" || r.status === "error") && r.finished_at) {
      lastFinishMs = Math.max(lastFinishMs, new Date(r.finished_at).getTime());
    }
  }
  if (lastFinishMs < 0 || nowMs - lastFinishMs > WRAPUP_WAIT_MS) return false;
  const reflected = thread.messages.some(
    (m) => m.role === "assistant" && new Date(m.created_at).getTime() >= lastFinishMs,
  );
  return !reflected;
}

export function HuntView() {
  const { data: session } = useSession();
  const userEmail = session?.user?.email ?? null;
  const [hunts, setHunts] = useState<HuntSummary[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [thread, setThread] = useState<HuntThread | null>(null);
  const [draft, setDraft] = useState("");
  // In-flight sends, keyed by the hunt they were sent in ("new" until the first
  // turn creates the hunt). The optimistic user bubble and the working indicator
  // render only in the OWNING hunt's transcript — switching hunts mid-turn must
  // not carry them along, and a turn finishing in another hunt must not touch
  // the visible thread.
  const [pendings, setPendings] = useState<Record<string, string>>({});
  // Which hunt is on screen right now — async completions check this before
  // writing thread state, so a stale response can never replace another hunt's
  // transcript.
  const activeIdRef = useRef<number | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const scroller = useRef<HTMLDivElement | null>(null);
  // The open proposal card's live edits, mirrored here so the next chat message
  // can carry them to Peon. Reset whenever the editable proposal changes so a
  // stale card's terms never ride along with a message under a newer proposal.
  const cardStateRef = useRef<HuntCardState | null>(null);

  const refreshHunts = useCallback(async () => {
    try {
      setHunts(await getHunts());
    } catch {
      /* transient — the rail just stays as-is */
    }
  }, []);

  useEffect(() => {
    activeIdRef.current = activeId;
  }, [activeId]);

  const loadThread = useCallback(async (id: number) => {
    try {
      const t = await getHunt(id);
      // Only land the result if this hunt is still the one on screen — the
      // founder may have switched (or clicked New hunt) while it was in flight.
      if (activeIdRef.current === id) setThread(t);
    } catch {
      /* transient — keep the last good thread */
    }
  }, []);

  // Initial load: hunt list, then open the thread the founder last had open
  // (restored from localStorage) — or the most-recently-active one when there is
  // no valid saved id. Reruns when the email resolves; the `cur != null` guard
  // keeps it from clobbering a thread the founder already opened or created.
  useEffect(() => {
    let cancelled = false;
    getHunts()
      .then((list) => {
        if (cancelled) return;
        setHunts(list);
        if (list.length === 0) return;
        setActiveId((cur) => {
          if (cur != null) return cur;
          const saved = readLastHunt(userEmail);
          return saved != null && list.some((h) => h.id === saved) ? saved : list[0].id;
        });
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [userEmail]);

  // Remember the open thread so a reload returns to it. newHunt() nulls activeId
  // for the blank composer; don't persist that (a reload then restores the last
  // real thread, which is the useful default).
  useEffect(() => {
    if (activeId != null) writeLastHunt(userEmail, activeId);
  }, [activeId, userEmail]);

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
    // A round just finished but Peon's async wrap-up hasn't landed — keep the
    // fast cadence so the reflection surfaces without a manual reload.
    const awaitingWrapup = !active && wrapupPending(thread, Date.now());
    const awaitingScores =
      !active && thread.results.some((r) => r.triage === "kept" && r.gem_score == null);
    const period = active || awaitingWrapup ? 2000 : awaitingScores ? 30000 : 0;
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

  // Drop the mirrored card state when the editable proposal changes (or clears):
  // ProposalCard re-publishes on mount, so a fresh card immediately refills it.
  useEffect(() => {
    cardStateRef.current = null;
  }, [proposalMsgId]);

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
    async (
      messageId: number,
      terms: string[],
      competitorApps: string[],
      settings?: HuntSettings,
      intent?: string,
    ) => {
      if (activeId == null) return;
      const round = await postHuntRound(activeId, {
        message_id: messageId,
        terms,
        competitor_apps: competitorApps,
        settings,
        intent,
      });
      mergeRound(activeId, round);
      await loadThread(activeId);
      refreshHunts();
    },
    [activeId, loadThread, refreshHunts, mergeRound],
  );

  const clearPending = useCallback((key: string) => {
    setPendings((p) => {
      if (!(key in p)) return p;
      const next = { ...p };
      delete next[key];
      return next;
    });
  }, []);

  const send = useCallback(async () => {
    const message = draft.trim();
    const key = activeId == null ? "new" : String(activeId);
    if (!message || pendings[key] != null) return;
    setDraft("");
    setPendings((p) => ({ ...p, [key]: message })); // instant user bubble in THIS hunt
    try {
      if (activeId == null) {
        const turn = await postHunt(message);
        // The hunt now has a real id: move the pending bubble onto it before
        // switching, so it stays visible while the thread loads.
        setPendings((p) => {
          const next = { ...p, [String(turn.hunt_id)]: message };
          delete next.new;
          return next;
        });
        activeIdRef.current = turn.hunt_id;
        setActiveId(turn.hunt_id);
        await loadThread(turn.hunt_id);
        refreshHunts();
        clearPending(String(turn.hunt_id));
      } else {
        // Carry the open proposal card's live edits so Peon reasons about the
        // on-screen terms; only when a proposal is actually open and edited.
        const cardState = proposalMsgId != null && cardStateRef.current ? cardStateRef.current : undefined;
        await postHuntMessage(activeId, message, cardState);
        await loadThread(activeId);
        clearPending(key);
      }
    } catch {
      // Restore the draft so the founder can retry — but only if they're still
      // on the hunt the message belongs to.
      if (activeIdRef.current === activeId) setDraft(message);
      clearPending(key);
    }
  }, [draft, pendings, activeId, loadThread, refreshHunts, proposalMsgId, clearPending]);

  // The pending send that belongs to the hunt on screen (if any). Everything
  // optimistic — bubble, working pulse, composer lock — keys off this, so a
  // send in another hunt never bleeds into the visible transcript.
  const pendingHere = pendings[activeId == null ? "new" : String(activeId)] ?? null;
  const sendingHere = pendingHere != null;

  // Keep the transcript pinned to the newest message: jump on thread switch and
  // whenever a message lands or a send starts. Round polling refreshes don't
  // change the message count, so they never yank the founder's scroll position.
  const messageCount = thread?.messages.length ?? 0;
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [activeId, messageCount, pendingHere]);

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

  // Delete a hunt and everything under it. Confirms first (irreversible), then
  // drops it from the rail; if it was the one on screen, fall back to the next
  // most-recent hunt (the rail is activity-sorted) or the blank composer.
  const removeHunt = useCallback(
    async (id: number) => {
      if (typeof window !== "undefined" &&
          !window.confirm("Delete this hunt and its results? This cannot be undone.")) {
        return;
      }
      try {
        await deleteHunt(id);
      } catch {
        return; // transient — leave the rail as-is so the founder can retry
      }
      const remaining = hunts.filter((h) => h.id !== id);
      setHunts(remaining);
      if (activeIdRef.current === id) {
        const fallback = remaining[0]?.id ?? null;
        setActiveId(fallback);
        if (fallback == null) setThread(null);
      }
    },
    [hunts],
  );

  return (
    <main style={{ maxWidth: 1100, margin: "0 auto", padding: "24px 22px 80px", display: "flex", gap: 24, alignItems: "flex-start", flexWrap: "wrap" }}>
      {/* Left rail — hunt history */}
      <aside style={{ flex: "0 0 240px", minWidth: 220, display: "flex", flexDirection: "column", gap: 12 }}>
        <Button variant="ghost" onClick={newHunt} style={{ justifyContent: "flex-start" }}>
          <IconPlus size={14} stroke={1.75} />
          New hunt
        </Button>
        <div style={{ display: "flex", flexDirection: "column" }}>
          {/* The delete control sits on top of the selectable row (nested
              buttons are invalid HTML) and only shows on hover / keyboard focus. */}
          <style>{`.hunt-row .hunt-del { opacity: 0; }
                   .hunt-row:hover .hunt-del, .hunt-del:focus-visible { opacity: 1; }`}</style>
          {hunts.map((h) => {
            const active = h.id === activeId;
            return (
              <div key={h.id} className="hunt-row" style={{ position: "relative", display: "flex" }}>
                <button
                  type="button"
                  onClick={() => setActiveId(h.id)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    textAlign: "left",
                    width: "100%",
                    padding: "10px 28px 10px 10px",
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
                      {fmtDate(h.last_activity)}
                    </span>
                  </span>
                </button>
                <button
                  type="button"
                  className="hunt-del"
                  aria-label="Delete hunt"
                  title="Delete hunt"
                  onClick={() => removeHunt(h.id)}
                  style={{
                    position: "absolute",
                    top: "50%",
                    right: 6,
                    transform: "translateY(-50%)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    padding: 4,
                    border: "none",
                    background: "transparent",
                    color: "var(--muted)",
                    cursor: "pointer",
                  }}
                >
                  <IconTrash size={14} stroke={1.75} />
                </button>
              </div>
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
                  onStart={(terms, competitors) =>
                    startRound(m.id, terms, competitors, m.proposal?.settings, m.proposal?.intent)
                  }
                  onChange={(terms, competitors) => {
                    cardStateRef.current = { terms, competitor_apps: competitors };
                  }}
                />
              )}
              {(roundsByMsg.map.get(m.id) ?? []).map((r) => (
                <RoundBlock key={r.id} round={r} results={resultsFor(r.id)} userEmail={userEmail} />
              ))}
            </div>
          ))}
          {roundsByMsg.orphans.map((r) => (
            <RoundBlock key={r.id} round={r} results={resultsFor(r.id)} userEmail={userEmail} />
          ))}
          {pendingHere != null && (
            <MessageBubble message={{ id: -1, role: "user", content: pendingHere, proposal: null, tool_log: null, created_at: "" }} />
          )}
          {sendingHere && <PeonWorking />}
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
            <Button onClick={send} disabled={sendingHere || draft.trim() === ""}>
              {sendingHere ? "Sending…" : "Send"}
            </Button>
          </div>
        </div>
      </section>
    </main>
  );
}

/** Peon's in-progress state: a labelled bubble that pulses while the agent turn
 *  (Gemini + tool calls, up to ~90s with web browsing) runs server-side. */
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
  const hasText = message.content.trim() !== "";
  // A proposal-only assistant turn (no reply text) shows just the Peon label and
  // its card — never an empty bordered box. An empty user bubble renders nothing.
  if (!hasText && toolLog.length === 0 && isUser) return null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, alignItems: isUser ? "flex-end" : "flex-start" }}>
      {!isUser && <span className="eyebrow">Peon</span>}
      {hasText && (
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
      )}
      {toolLog.length > 0 && (
        <div className="t-xs font-mono" style={{ maxWidth: "82%", color: "var(--muted)" }}>
          {toolLog.join(" · ")}
        </div>
      )}
    </div>
  );
}
