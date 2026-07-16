"""Run ONE hunt round to completion — the hunt_daemon subprocess body.
Owns terminal status for both the round row and the pipeline job on every
exit path (the daemon's safety net only catches a killed process)."""
import argparse
import logging
import os
import signal
import sys
from functools import partial

from agf.db.engine import begin, get_engine
from agf.db.hunts import (
    assistant_message_since_finish,
    compose_round_message,
    finish_round,
    insert_round_message,
    round_summary_counts,
)
from agf.db.pipeline_jobs import set_job_status
from agf.discovery.hunt import HUNT_TERM_LANES, run_hunt_finish, run_hunt_sweep

log = logging.getLogger("agf.hunt_one")


class HuntTimeout(BaseException):
    """SIGALRM watchdog — BaseException so no except-Exception swallows it
    (lesson: EnrichDeadline)."""


def _build_extractors(k: int):
    from agf.extract.facade import Extractor
    from agf.extract.proxy import ProxyPool
    proxies = ProxyPool.list_from_env()
    k = max(1, k)
    if not proxies:
        return [Extractor(proxies=ProxyPool(proxies))]
    if len(proxies) >= k:
        slices = [proxies[i::k] for i in range(k)]
        return [Extractor(proxies=ProxyPool(sl)) for sl in slices]
    return [Extractor(proxies=ProxyPool(proxies)) for _ in range(k)]


def main(argv=None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--round-id", type=int, required=True)
    ap.add_argument("--job-id", type=int, required=True)
    ap.add_argument("--timeout", type=int, default=1800)         # scrape budget
    ap.add_argument("--finish-timeout", type=int, default=600)   # phase-5 budget
    args = ap.parse_args(argv)

    # One SIGALRM handler serves both windows; `phase` names the active budget so
    # the HuntTimeout message says which half overran (scrape vs finish).
    phase = {"name": "scrape", "budget": args.timeout}

    def _alarm(signum, frame):
        raise HuntTimeout(
            f"hunt round exceeded {phase['budget']}s in {phase['name']}")

    signal.signal(signal.SIGALRM, _alarm)

    engine = get_engine()
    import httpx
    from agf.enrich_full.appstore import lookup_google_play, lookup_itunes_batch
    http_client = httpx.Client(timeout=20, follow_redirects=True)
    try:
        extractors = _build_extractors(HUNT_TERM_LANES)

        # --- Scrape window: phases 0-4. Results persist incrementally. ---
        signal.alarm(args.timeout)
        try:
            ctx = run_hunt_sweep(engine, extractors, args.round_id)
        except HuntTimeout as e:
            # The sweep didn't finish, but every persisted page is real. The lane
            # threads may still be alive — do NOT run phase 5 after a mid-sweep
            # timeout. The partial marker is already in progress (run_hunt_sweep
            # set it in _Progress._state so lane flushes can't clobber it). Commit
            # the round 'done' (partial), never 'error'.
            signal.alarm(0)
            log.warning("hunt round %d sweep hit the scrape budget: %s",
                        args.round_id, e)
            with begin(engine) as conn:
                finish_round(conn, args.round_id, status="done")
                set_job_status(conn, args.job_id, "done")
            _complete_round(engine, args, http_client)
            log.info("hunt round %d done (partial sweep)", args.round_id)
            return 0
        signal.alarm(0)

        # --- Finish window: phase 5, best-effort. A timeout or any error here
        # must NOT fail a completed scrape (the fast tail already ran enrich +
        # mint inside run_hunt_finish's finally before propagating). ---
        phase["name"], phase["budget"] = "finish", args.finish_timeout
        signal.alarm(args.finish_timeout)
        try:
            stats = run_hunt_finish(
                engine, args.round_id, ctx,
                lookup_ios=partial(lookup_itunes_batch, http_client, pause=3.0),
                lookup_android=lookup_google_play)
        except HuntTimeout as e:
            log.warning("hunt round %d finish hit the finish budget: %s",
                        args.round_id, e)
            stats = None
        except Exception:  # noqa: BLE001 — a finish error must not fail a done scrape
            log.exception("hunt round %d finish failed (results kept)",
                          args.round_id)
            stats = None
        finally:
            # Disarm BEFORE finish_round + the (up to 150s) completion hook so a
            # late alarm can't overwrite 'done' with 'error'.
            signal.alarm(0)

        with begin(engine) as conn:
            finish_round(conn, args.round_id, status="done")
            set_job_status(conn, args.job_id, "done")
        _complete_round(engine, args, http_client)
        log.info("hunt round %d done: %s", args.round_id, stats)
        return 0
    except HuntTimeout as e:
        # A stray alarm outside both (already-disarmed) windows.
        _fail(engine, args, str(e))
        return 1
    except Exception as e:  # noqa: BLE001 — terminal-status parity on all paths
        log.exception("hunt round %d failed", args.round_id)
        _fail(engine, args, str(e)[:500])
        return 1
    finally:
        signal.alarm(0)
        http_client.close()


def _complete_round(engine, args, http_client) -> None:
    """Round + job are already committed 'done' and the watchdog is disarmed.
    Run the completion hook; on failure write the canned fallback so a round is
    never silent — but only when the API didn't already write Peon's wrap-up (a
    lost/timed-out response still means the message landed)."""
    if _notify_completed(args.round_id, http_client):
        return
    with begin(engine) as conn:
        try:
            if not assistant_message_since_finish(conn, args.round_id):
                counts = round_summary_counts(conn, args.round_id)
                insert_round_message(
                    conn, args.round_id,
                    compose_round_message(args.round_id, "done", counts))
        except Exception:  # message is a convenience; the round row is the truth
            log.exception("round completion message failed")


def _notify_completed(round_id: int, http_client) -> bool:
    """POST the internal round-completed hook; True when the API wrote Peon's
    wrap-up message. Any failure returns False and the caller writes the
    canned fallback, so a round never finishes silently."""
    base = os.environ.get("AGF_API_URL", "").rstrip("/")
    token = os.environ.get("AGF_API_TOKEN", "")
    if not base or not token:
        return False
    try:
        resp = http_client.post(
            f"{base}/api/internal/hunts/rounds/{round_id}/completed",
            # 150s > the API's worst-case successful turn (90s Gemini budget +
            # 15s write) — a read timeout under a still-succeeding turn would
            # write the canned fallback ON TOP of Peon's committed message.
            headers={"Authorization": f"Bearer {token}"},
            timeout=150)
        if resp.status_code != 200:
            log.warning("completion hook returned %d for round %d",
                        resp.status_code, round_id)
        return resp.status_code == 200
    except Exception:  # noqa: BLE001 — fallback message covers any failure
        log.exception("completion hook failed for round %d", round_id)
        return False


def _fail(engine, args, msg: str) -> None:
    with begin(engine) as conn:
        finish_round(conn, args.round_id, status="error", error=msg)
        set_job_status(conn, args.job_id, "error", error=msg)
        try:
            insert_round_message(conn, args.round_id,
                                 compose_round_message(args.round_id, "error", {}, error=msg))
        except Exception:  # message is a convenience; the round row is the truth
            log.exception("round failure message failed")


if __name__ == "__main__":
    sys.exit(main())
