"""Run ONE hunt round to completion — the hunt_daemon subprocess body.
Owns terminal status for both the round row and the pipeline job on every
exit path (the daemon's safety net only catches a killed process)."""
import argparse
import logging
import signal
import sys
from functools import partial

from agf.db.engine import begin, get_engine
from agf.db.hunts import (
    compose_round_message,
    finish_round,
    insert_round_message,
    round_summary_counts,
)
from agf.db.pipeline_jobs import set_job_status
from agf.discovery.hunt import HUNT_TERM_LANES, run_hunt_round

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
    ap.add_argument("--timeout", type=int, default=1800)
    args = ap.parse_args(argv)

    def _alarm(signum, frame):
        raise HuntTimeout(f"hunt round exceeded {args.timeout}s")

    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(args.timeout)

    engine = get_engine()
    import httpx
    from agf.enrich_full.appstore import lookup_google_play, lookup_itunes_batch
    http_client = httpx.Client(timeout=20, follow_redirects=True)
    try:
        extractors = _build_extractors(HUNT_TERM_LANES)
        stats = run_hunt_round(
            engine, extractors, args.round_id,
            lookup_ios=partial(lookup_itunes_batch, http_client, pause=3.0),
            lookup_android=lookup_google_play)
        with begin(engine) as conn:
            finish_round(conn, args.round_id, status="done")
            set_job_status(conn, args.job_id, "done")
            try:
                counts = round_summary_counts(conn, args.round_id)
                insert_round_message(conn, args.round_id,
                                     compose_round_message(args.round_id, "done", counts))
            except Exception:  # message is a convenience; the round row is the truth
                log.exception("round completion message failed")
        log.info("hunt round %d done: %s", args.round_id, stats)
        return 0
    except HuntTimeout as e:
        _fail(engine, args, str(e))
        return 1
    except Exception as e:  # noqa: BLE001 — terminal-status parity on all paths
        log.exception("hunt round %d failed", args.round_id)
        _fail(engine, args, str(e)[:500])
        return 1
    finally:
        signal.alarm(0)
        http_client.close()


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
