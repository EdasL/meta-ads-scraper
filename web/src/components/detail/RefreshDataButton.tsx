"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { postEnrich, getEnrichStatus, type EnrichStatus } from "@/lib/api";

type UiState = "idle" | "running" | "updated" | "error";

/**
 * On-demand "Refresh data" for one gem. Triggers backend enrichment (re-pulls
 * ads, store data, media, funnel + re-scores), polls the job status, and calls
 * onRefreshed() when it finishes so the parent re-fetches the dossier.
 */
export function RefreshDataButton({
  advertiserId,
  onRefreshed,
}: {
  advertiserId: number;
  onRefreshed: () => void;
}) {
  const [state, setState] = useState<UiState>("idle");
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (timer.current) {
      clearInterval(timer.current);
      timer.current = null;
    }
  }, []);

  const apply = useCallback(
    (s: EnrichStatus) => {
      if (s.status === "running") {
        setState("running");
      } else if (s.status === "done") {
        stopPolling();
        setState("updated");
        onRefreshed(); // re-fetch the dossier with fresh data
        setTimeout(() => setState("idle"), 2500);
      } else if (s.status === "error") {
        stopPolling();
        setError(s.error_text ?? "Enrichment failed");
        setState("error");
      }
    },
    [onRefreshed, stopPolling],
  );

  const poll = useCallback(() => {
    if (timer.current) return;
    timer.current = setInterval(async () => {
      try {
        apply(await getEnrichStatus(advertiserId));
      } catch {
        /* transient — keep polling */
      }
    }, 3000);
  }, [advertiserId, apply]);

  // Resume polling if a job is already running when the page loads.
  useEffect(() => {
    let cancelled = false;
    getEnrichStatus(advertiserId)
      .then((s) => {
        if (!cancelled && s.status === "running") {
          setState("running");
          poll();
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
      stopPolling();
    };
  }, [advertiserId, poll, stopPolling]);

  const onClick = useCallback(async () => {
    setError(null);
    setState("running");
    try {
      apply(await postEnrich(advertiserId));
      poll();
    } catch {
      setState("error");
      setError("Could not start refresh");
    }
  }, [advertiserId, apply, poll]);

  const label =
    state === "running"
      ? "Refreshing…"
      : state === "updated"
        ? "Updated"
        : state === "error"
          ? "Refresh failed"
          : "Refresh data";

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={state === "running"}
      title={error ?? "Re-pull this app's ads, store data, and funnel"}
      className="t-sm"
      style={{
        border: "1.5px solid var(--ink)",
        background: "var(--surface-1)",
        padding: "5px 10px",
        cursor: state === "running" ? "default" : "pointer",
        opacity: state === "running" ? 0.6 : 1,
      }}
    >
      {label}
    </button>
  );
}
