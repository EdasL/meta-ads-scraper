"""audit_crowded_keywords.py — find + rest ACTIVE keywords whose finds are mostly
embedding-space clones (crowding penalty §G).

An ACTIVE app keyword is an OFFENDER when its kept candidates are dominated by the
crowded tail:
    kept_n        = kept candidates for the keyword whose crowding IS NOT NULL
    crowded_share = (kept candidates with crowding >= tail) / kept_n
    offender      = kept_n >= min_kept AND crowded_share >= share
These are the trawling nets (e.g. "calorie tracking") that promote on clone volume.

Default is a DRY RUN: one report line per offender plus a summary count, mutating
nothing. `--apply` rests each offender exactly the way the flywheel does when an active
keyword goes dry (record_keyword_sweep._rest): status='resting', dry_streak=0,
rest_count += 1, next_eligible_at = now() + cooldown — so it leaves the daily rotation.

CAVEAT (whole-branch review 2026-07-10): the resting→active REVIVE path still runs on
RAW yield (record_keyword_sweep revives on any new candidate; this sweep's finds are
not embedded yet, so an uncrowded check there would degrade open and do nothing). A
rested offender that keeps finding clones WILL wake and re-activate — re-run this
audit periodically until the lifecycle gains a durable crowded-share check.

Run:
    PYTHONPATH=src AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf \\
    .venv/bin/python -m scripts.audit_crowded_keywords          # dry run
    ... .venv/bin/python -m scripts.audit_crowded_keywords --apply
"""
import argparse
from dataclasses import dataclass

from sqlalchemy import text

from agf.db import dynamic_keywords as dk
from agf.db.engine import get_engine


@dataclass
class Offender:
    keyword_id: int
    keyword: str
    rest_count: int
    kept_n: int
    crowded_n: int
    crowded_share: float
    top_names: list[str]


def find_offenders(conn, *, min_kept: int, share: float, tail: float) -> list[Offender]:
    """ACTIVE app keywords whose kept, crowding-scored candidates are mostly crowded.

    Read-only. kept_n counts kept candidates with crowding NOT NULL (the join to
    candidate_embeddings plus the NOT NULL filter); crowded_share is crowded/kept_n.
    NULL-crowding candidates are excluded from BOTH numerator and denominator, so a
    keyword whose crowding has not been populated yet is never flagged (degrade open)."""
    rows = conn.execute(text(
        "SELECT k.id, k.keyword, k.rest_count, "
        "       count(*) FILTER (WHERE e.crowding IS NOT NULL) AS kept_n, "
        "       count(*) FILTER (WHERE e.crowding >= :tail)   AS crowded_n "
        "FROM discovery.dynamic_keywords k "
        "JOIN discovery.candidates c "
        "  ON c.source_keyword = k.keyword AND c.status = 'kept' "
        "JOIN discovery.candidate_embeddings e ON e.candidate_id = c.id "
        "WHERE k.status = 'active' AND k.kind = 'app' "
        "GROUP BY k.id, k.keyword, k.rest_count "
        "HAVING count(*) FILTER (WHERE e.crowding IS NOT NULL) >= :min_kept "
        "   AND count(*) FILTER (WHERE e.crowding >= :tail)::float "
        "       / NULLIF(count(*) FILTER (WHERE e.crowding IS NOT NULL), 0) >= :share "
        "ORDER BY crowded_n DESC, kept_n DESC, k.keyword"),
        {"tail": tail, "min_kept": min_kept, "share": share}).all()

    offenders = []
    for r in rows:
        top_names = [n for (n,) in conn.execute(text(
            "SELECT c.page_name FROM discovery.candidates c "
            "JOIN discovery.candidate_embeddings e ON e.candidate_id = c.id "
            "WHERE c.source_keyword = :kw AND c.status = 'kept' AND e.crowding >= :tail "
            "ORDER BY e.crowding DESC, c.id LIMIT 3"),
            {"kw": r.keyword, "tail": tail}).all()]
        offenders.append(Offender(
            keyword_id=r.id, keyword=r.keyword, rest_count=r.rest_count,
            kept_n=r.kept_n, crowded_n=r.crowded_n,
            crowded_share=r.crowded_n / r.kept_n, top_names=top_names))
    return offenders


def apply_rests(conn, offenders: list[Offender]) -> int:
    """Rest each offender, mirroring record_keyword_sweep._rest. Returns the count actually
    rested (sum of affected rows), which can be < len(offenders) — see the race guard below.

    cooldown_days(new_rest_count - 1) reproduces the flywheel's cooldown exactly: a
    keyword's first rest waits the base cooldown, doubling on each subsequent rest.
    The cooldown is computed in Python and bound as an integer day count via
    make_interval(days => :cd) — no bare `%` / interval literal reaches the executed SQL.

    The `status='active'` guard makes each rest a no-op if a concurrent worker rested or
    revived the keyword between find_offenders' read and this write: rest_count and status
    were captured under an active keyword, so clobbering a now-resting row would double-count
    the rest (stale rest_count) or resurrect a just-revived one."""
    stmt = text(
        "UPDATE discovery.dynamic_keywords "
        "SET status='resting', dry_streak=0, rest_count=:rc, "
        "    next_eligible_at = now() + make_interval(days => :cd) "
        "WHERE id=:i AND status='active'")
    rested = 0
    for o in offenders:
        new_rc = o.rest_count + 1
        rested += conn.execute(stmt, {"rc": new_rc, "cd": dk.cooldown_days(new_rc - 1),
                                      "i": o.keyword_id}).rowcount
    return rested


def _report(offenders: list[Offender], *, applied: bool, rested: int | None = None) -> None:
    print(f"=== Crowded-keyword audit ({'APPLY' if applied else 'dry run'}) ===\n")
    if not offenders:
        print("No crowded offenders found.")
        return
    for o in offenders:
        names = "; ".join(o.top_names) if o.top_names else "(none)"
        print(f"  {o.keyword!r:<40} kept_n={o.kept_n:<3} "
              f"crowded_share={o.crowded_share:.2f}  top: {names}")
    if applied:
        # rested is the real affected-row count; it can trail len(offenders) when a
        # concurrent worker moved a keyword out of 'active' before the rest landed.
        print(f"\n{rested} rested (of {len(offenders)} offenders).")
    else:
        print(f"\n{len(offenders)} offenders (dry run — nothing changed).")


def main() -> None:
    ap = argparse.ArgumentParser(description="Audit + rest crowded ACTIVE keywords.")
    ap.add_argument("--apply", action="store_true",
                    help="rest the offenders (default: dry-run report only)")
    ap.add_argument("--min-kept", type=int, default=3,
                    help="minimum crowding-scored kept candidates to consider (default 3)")
    ap.add_argument("--share", type=float, default=0.6,
                    help="crowded-share threshold for an offender (default 0.6)")
    ap.add_argument("--tail", type=float, default=dk.crowding_tail(),
                    help="crowding >= tail is 'crowded' (default AGF_CROWDING_TAIL / 0.80)")
    args = ap.parse_args()

    # get_engine() already resolves AGF_DATABASE_URL → AGF_TEST_DB_URL on its own.
    engine = get_engine()
    with engine.begin() as conn:
        offenders = find_offenders(
            conn, min_kept=args.min_kept, share=args.share, tail=args.tail)
        rested = None
        if args.apply:
            rested = apply_rests(conn, offenders)
        else:
            conn.rollback()  # belt-and-suspenders: dry run commits nothing
        _report(offenders, applied=args.apply, rested=rested)


if __name__ == "__main__":
    main()
