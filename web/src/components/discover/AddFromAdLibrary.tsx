"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { addGemFromLink, getAddGemStatus, type AddGemStatus } from "@/lib/api";
import { Button } from "@/components/ui";

/**
 * Paste a Meta Ad Library link for an advertiser and Field Desk scrapes,
 * triages, enriches and scores it into a gem.
 *
 * Progressive disclosure rather than a modal: the header carries one button,
 * and the paste field opens in place beneath it. The work runs on the worker
 * VM, so the API hands back a job and this polls it, showing the phase the
 * worker publishes ("Pulling ads", "Checking the store listing", …). That is
 * real state, not a decorative spinner — a scrape plus an enrich takes a while
 * and the phase is what makes the wait legible.
 *
 * A rejected link keeps the field populated so the person can fix and resend.
 */

const POLL_MS = 2000;

/** Terminal states stop the poll loop. */
function isDone(s: AddGemStatus | null): boolean {
  return s?.status === "done" || s?.status === "error";
}

export function AddFromAdLibrary({ onAdded }: { onAdded?: () => void }) {
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [job, setJob] = useState<AddGemStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  // Fires the feed refetch exactly once per completed job.
  const announced = useRef<number | null>(null);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  // Poll while a job is in flight. The interval clears on unmount and on any
  // terminal status, so navigating away never leaves a timer running.
  useEffect(() => {
    if (!job || isDone(job)) return;
    let alive = true;
    const id = setInterval(async () => {
      try {
        const next = await getAddGemStatus(job.job_id);
        if (alive) setJob(next);
      } catch {
        // A dropped poll is not a failed job — the next tick retries. The
        // worker owns the terminal status either way.
      }
    }, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [job]);

  useEffect(() => {
    if (job?.status !== "done" || announced.current === job.job_id) return;
    announced.current = job.job_id;
    onAdded?.();
  }, [job, onAdded]);

  const submit = useCallback(async () => {
    const trimmed = url.trim();
    if (!trimmed || submitting) return;
    setSubmitting(true);
    setError(null);
    setJob(null);
    try {
      setJob(await addGemFromLink(trimmed));
    } catch (e) {
      // The API's message names what to paste instead, so it is shown as-is.
      setError(e instanceof Error ? e.message : "That link could not be added.");
    } finally {
      setSubmitting(false);
    }
  }, [url, submitting]);

  const reset = useCallback(() => {
    setOpen(false);
    setUrl("");
    setJob(null);
    setError(null);
  }, []);

  if (!open) {
    return (
      <Button onClick={() => setOpen(true)} style={{ flexShrink: 0 }}>
        Add from Ad Library
      </Button>
    );
  }

  const running = job !== null && !isDone(job);
  const busy = submitting || running;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--sp-2)",
        minWidth: 0,
        flex: "1 1 340px",
        maxWidth: 520,
      }}
    >
      {/* The browser default placeholder grey does not clear 4.5:1 on
          --surface-2; --muted is the lightest token that does. */}
      <style>{`.agf-add-input::placeholder { color: var(--muted); opacity: 1 }`}</style>
      <div style={{ display: "flex", gap: "var(--sp-2)", minWidth: 0 }}>
        <input
          ref={inputRef}
          type="url"
          value={url}
          disabled={busy}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
            if (e.key === "Escape" && !busy) reset();
          }}
          placeholder="Paste a Meta Ad Library link"
          aria-label="Meta Ad Library link"
          className="agf-add-input"
          style={{
            flex: 1,
            minWidth: 0,
            padding: "9px 12px",
            border: "1.5px solid var(--ink)",
            borderRadius: "var(--radius-sm)",
            background: "var(--surface-2)",
            color: "var(--ink)",
            fontSize: "var(--fs-sm)",
            fontFamily: "var(--font-mono), monospace",
          }}
        />
        <Button onClick={submit} disabled={busy || url.trim() === ""}>
          {busy ? "Working" : "Add"}
        </Button>
        <Button variant="ghost" onClick={reset} disabled={running}>
          Cancel
        </Button>
      </div>

      <p
        aria-live="polite"
        className="t-sm"
        style={{ margin: 0, color: "var(--muted)", minHeight: "1.125rem" }}
      >
        {error ? (
          <span style={{ color: "var(--down)" }}>{error}</span>
        ) : job === null ? (
          submitting ? (
            "Sending…"
          ) : (
            ""
          )
        ) : job.status === "error" ? (
          <span style={{ color: "var(--down)" }}>
            {job.error_text ?? "The scrape did not finish."}
          </span>
        ) : running ? (
          <>
            {job.already_known ? "Field Desk already had this advertiser. " : ""}
            {job.phase ?? "Queued"}
            <Dots />
          </>
        ) : (
          <Result job={job} />
        )}
      </p>
    </div>
  );
}

/** The finished job, inline. One advertiser is a sentence, not a card. */
function Result({ job }: { job: AddGemStatus }) {
  const name = job.page_name?.trim() || "This advertiser";
  return (
    <>
      <span style={{ color: "var(--ink)" }}>{name}</span> is in your feed
      {job.ad_count > 0 ? ` with ${job.ad_count} ${job.ad_count === 1 ? "ad" : "ads"}` : ""}.{" "}
      <Link href={`/gems/${job.advertiser_id}`} style={{ color: "var(--ink)" }}>
        Open it
      </Link>
      {job.triage_note ? <span>{` ${job.triage_note}`}</span> : null}
    </>
  );
}

/**
 * Three dots that cycle while a phase is in flight. Motion here conveys "still
 * working" between phase changes, which can be a minute apart; under
 * prefers-reduced-motion the animation is suppressed and the dots hold still.
 */
function Dots() {
  return (
    <span aria-hidden="true">
      <style>{`
        @keyframes agf-dot { 0%, 80%, 100% { opacity: 0.25 } 40% { opacity: 1 } }
        .agf-dot { animation: agf-dot 1.4s ease-out infinite }
        @media (prefers-reduced-motion: reduce) { .agf-dot { animation: none; opacity: 0.6 } }
      `}</style>
      <span className="agf-dot">.</span>
      <span className="agf-dot" style={{ animationDelay: "0.2s" }}>
        .
      </span>
      <span className="agf-dot" style={{ animationDelay: "0.4s" }}>
        .
      </span>
    </span>
  );
}
