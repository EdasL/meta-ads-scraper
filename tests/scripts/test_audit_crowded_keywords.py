"""Audit + rest crowded ACTIVE keywords (crowding penalty §G / task T6).

An ACTIVE app keyword whose kept finds are mostly embedding-space clones
(kept_n >= min_kept AND crowded_share >= share) is an offender: it trawls a
saturated niche. The audit lists offenders (dry-run, mutates nothing) and
--apply rests them for a cooldown so they leave the rotation and can wake later
under the new uncrowded-yield promotion rules.

Shared agf_it DB — everything is scoped to AUDITCROWD sentinel keywords and a
high sentinel candidate-id range; pg_conn rolls it all back."""
import sys
from datetime import datetime, timezone

from sqlalchemy import text

from agf.db import dynamic_keywords as dk
from scripts import audit_crowded_keywords as audit
from scripts.audit_crowded_keywords import apply_rests, find_offenders

CAND_BASE = 9_310_000_000
DIM = 4


def _row(conn, keyword):
    return conn.execute(text(
        "SELECT status, dry_streak, rest_count, next_eligible_at "
        "FROM discovery.dynamic_keywords WHERE keyword = :k"),
        {"k": keyword}).one()


def _seed_active(conn, keyword, *, offset, crowdings, rest_count=0,
                 kind="app", status="active"):
    """Create a keyword (default ACTIVE app) with one kept candidate per entry in
    `crowdings` (a float crowding to store, or None for a NULL-crowding embedding row).
    `kind`/`status` let a test seed a funnel or trial keyword with an identical crowded
    profile to prove find_offenders' scope filters exclude it. Ids are sentinel-scoped
    by `offset`."""
    dk.add_trial_keywords(conn, [keyword], kind=kind)
    conn.execute(text(
        "UPDATE discovery.dynamic_keywords "
        "SET status=:st, dry_streak=2, rest_count=:rc, sweeps_used=4, "
        "    new_candidates_total=:n WHERE keyword=:k AND kind=:kind"),
        {"st": status, "rc": rest_count, "n": len(crowdings), "k": keyword, "kind": kind})
    cand = text("INSERT INTO discovery.candidates "
                "(id, page_id, page_name, store, store_id, status, source_keyword) "
                "VALUES (:id, :pg, :nm, 'ios', :sid, 'kept', :kw)")
    emb = text("INSERT INTO discovery.candidate_embeddings "
               "(candidate_id, embedding, content_hash, dim, model, crowding) "
               "VALUES (:id, :e, 'sentinel', :d, 'test', :cr)")
    vec = [0.5] * DIM
    for i, cr in enumerate(crowdings):
        cid = CAND_BASE + offset + i
        conn.execute(cand, {"id": cid, "pg": f"pg{cid}", "nm": f"App {cid}",
                            "sid": f"s{cid}", "kw": keyword})
        conn.execute(emb, {"id": cid, "e": vec, "d": DIM, "cr": cr})


def _find(conn, **kw):
    kw.setdefault("min_kept", 3)
    kw.setdefault("share", 0.6)
    kw.setdefault("tail", dk.crowding_tail())
    return find_offenders(conn, **kw)


def test_find_offenders_flags_only_the_crowded_active(pg_conn, monkeypatch):
    monkeypatch.delenv("AGF_CROWDING_TAIL", raising=False)
    off = "AUDITCROWD offender"          # 4/5 crowded -> share 0.8
    clean = "AUDITCROWD clean"           # 0/5 crowded
    small = "AUDITCROWD small"           # 2/2 crowded but kept_n < 3
    _seed_active(pg_conn, off, offset=0, crowdings=[0.95, 0.9, 0.9, 0.85, 0.1])
    _seed_active(pg_conn, clean, offset=100, crowdings=[0.1] * 5)
    _seed_active(pg_conn, small, offset=200, crowdings=[0.9, 0.9])

    offenders = _find(pg_conn)
    got = {o.keyword: o for o in offenders if o.keyword.startswith("AUDITCROWD")}

    assert set(got) == {off}
    o = got[off]
    assert o.kept_n == 5
    assert o.crowded_n == 4
    assert abs(o.crowded_share - 0.8) < 1e-9
    assert len(o.top_names) == 3       # top-3 crowded app page_names, most crowded first
    assert all(n.startswith("App ") for n in o.top_names)


def test_null_crowding_excluded_from_kept_n(pg_conn, monkeypatch):
    # kept_n counts only crowding-NOT-NULL rows. 2 crowded + 3 NULL => kept_n 2 (< 3),
    # NOT an offender. If NULLs were counted, kept_n would be 5 and it would flag.
    monkeypatch.delenv("AGF_CROWDING_TAIL", raising=False)
    kw = "AUDITCROWD nulls"
    _seed_active(pg_conn, kw, offset=300, crowdings=[0.9, 0.9, None, None, None])
    assert [o.keyword for o in _find(pg_conn)] == []


def test_dry_run_find_mutates_nothing(pg_conn, monkeypatch):
    monkeypatch.delenv("AGF_CROWDING_TAIL", raising=False)
    kw = "AUDITCROWD dryrun"
    _seed_active(pg_conn, kw, offset=400, crowdings=[0.9] * 5)

    _find(pg_conn)  # dry run: read only

    assert _row(pg_conn, kw).status == "active"


def test_apply_rests_only_the_offender(pg_conn, monkeypatch):
    monkeypatch.delenv("AGF_CROWDING_TAIL", raising=False)
    off = "AUDITCROWD apply-off"
    clean = "AUDITCROWD apply-clean"
    _seed_active(pg_conn, off, offset=500, crowdings=[0.9] * 5, rest_count=0)
    _seed_active(pg_conn, clean, offset=600, crowdings=[0.1] * 5)

    offenders = _find(pg_conn)
    rested = apply_rests(pg_conn, offenders)
    assert rested == 1

    r = _row(pg_conn, off)
    assert r.status == "resting"
    assert r.dry_streak == 0
    assert r.rest_count == 1                     # rest_count + 1
    # First rest waits the base cooldown (mirrors record_keyword_sweep._rest:
    # cooldown_days(new_rest_count - 1) = cooldown_days(0)).
    expected = dk.cooldown_days(0)
    days = (r.next_eligible_at - datetime.now(timezone.utc)).total_seconds() / 86400
    assert abs(days - expected) < 1.0

    assert _row(pg_conn, clean).status == "active"  # non-offender untouched


def test_thresholds_are_overridable(pg_conn, monkeypatch):
    monkeypatch.delenv("AGF_CROWDING_TAIL", raising=False)
    kw = "AUDITCROWD halfshare"       # 3/6 crowded -> share 0.5
    _seed_active(pg_conn, kw, offset=700, crowdings=[0.9, 0.9, 0.9, 0.1, 0.1, 0.1])

    # Default share 0.6 -> not an offender.
    assert kw not in [o.keyword for o in _find(pg_conn)]
    # Lowered share 0.5 -> now flagged.
    assert kw in [o.keyword for o in _find(pg_conn, share=0.5)]


def test_find_offenders_scope_locked_to_active_app(pg_conn, monkeypatch):
    # A funnel keyword and a trial keyword, both with a fully crowded profile that would
    # flag if scope were ignored, must be excluded: find_offenders is WHERE status='active'
    # AND kind='app'. Only the plain active-app control is flagged.
    monkeypatch.delenv("AGF_CROWDING_TAIL", raising=False)
    ctrl = "AUDITCROWD scope-app"
    funnel = "AUDITCROWD scope-funnel"
    trial = "AUDITCROWD scope-trial"
    _seed_active(pg_conn, ctrl, offset=800, crowdings=[0.9] * 5)
    _seed_active(pg_conn, funnel, offset=900, crowdings=[0.9] * 5, kind="funnel")
    _seed_active(pg_conn, trial, offset=1000, crowdings=[0.9] * 5, status="trial")

    flagged = {o.keyword for o in _find(pg_conn) if o.keyword.startswith("AUDITCROWD")}
    assert flagged == {ctrl}


def test_apply_rests_skips_concurrently_rested(pg_conn, monkeypatch):
    # Race guard: find_offenders reads the keyword while ACTIVE, then a concurrent worker
    # rests it before apply_rests writes. The status='active' guard must leave that row
    # untouched (no stale-rest_count clobber) and count it as 0 rested.
    monkeypatch.delenv("AGF_CROWDING_TAIL", raising=False)
    off = "AUDITCROWD race-off"
    _seed_active(pg_conn, off, offset=1100, crowdings=[0.9] * 5, rest_count=0)

    offenders = _find(pg_conn)
    assert [o.keyword for o in offenders if o.keyword.startswith("AUDITCROWD")] == [off]

    # Simulate the concurrent worker resting it between the read and the write.
    pg_conn.execute(text("UPDATE discovery.dynamic_keywords SET status='resting' "
                         "WHERE keyword=:k"), {"k": off})

    rested = apply_rests(pg_conn, offenders)
    assert rested == 0

    r = _row(pg_conn, off)
    assert r.status == "resting"     # left exactly as the concurrent worker set it
    assert r.rest_count == 0         # NOT bumped to 1 — no stale-rest_count clobber
    assert r.dry_streak == 2         # untouched


def test_main_dry_run_smoke(pg_conn, monkeypatch, capsys):
    # End-to-end smoke of main(): argparse path + get_engine() + the begin/rollback
    # wrapper, in dry-run mode. main opens its own connection (not pg_conn), so it does
    # not see this txn's seed — it just has to run and print the dry-run header. Depending
    # on pg_conn keeps it skipped when AGF_TEST_DB_URL is unset and the schema migrated.
    monkeypatch.delenv("AGF_CROWDING_TAIL", raising=False)
    monkeypatch.setattr(sys, "argv", ["audit_crowded_keywords"])

    audit.main()

    out = capsys.readouterr().out
    assert "Crowded-keyword audit (dry run)" in out
