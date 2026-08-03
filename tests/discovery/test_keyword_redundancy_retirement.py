"""Redundancy grading (spec 2026-08-03): the dynamic-keyword retire pass
(dk.retire_redundant_keywords) and the static-keyword suppression overlay
(db/keyword_suppressions.py). A keyword is redundant when its kept catch is
mostly clone-cluster — crowded share >= AGF_KW_REDUNDANT_SHARE over at least
AGF_KW_REDUNDANT_MIN_KEPT embedded kept candidates.

Everything runs on the rolled-back `pg_conn` fixture: the graded rows are seeded
inside the test's own transaction, so a concurrent suite never sees them and no
sentinel teardown is needed. Ids still sit in a private high range so a manual
run against a populated DB can't trip the candidates (page_id, store, store_id)
unique key.

Assertions about the retire pass are membership-scoped rather than exact-list —
it grades EVERY active/resting keyword in the DB, and the shared test DB may
carry rows another suite committed."""
import itertools

import pytest
from sqlalchemy import text

from agf.db import dynamic_keywords as dk
from agf.db.keyword_suppressions import (load_suppressed_keywords,
                                         suppress_redundant_static_keywords)

CAND_BASE = 2_000_000_100     # sentinel candidate ids (one page_id each)
KW_BASE = 2_000_500           # sentinel dynamic_keywords ids

_CAND = text(
    "INSERT INTO discovery.candidates "
    "(id, page_id, page_name, store, store_id, status, source_keyword) "
    "VALUES (:id, :pg, 'redundancy sentinel', 'ios', :sid, 'kept', :kw)")
_EMB = text(
    "INSERT INTO discovery.candidate_embeddings "
    "(candidate_id, embedding, content_hash, dim, model, crowding) "
    "VALUES (:id, :e, 'sentinel', 2, 'test', :cr)")


@pytest.fixture(autouse=True)
def _knobs(monkeypatch):
    """Pin the graded thresholds to their documented defaults so ambient env
    (the worker's plist exports the same names) can never move the bar."""
    monkeypatch.setenv("AGF_CROWDING_TAIL_CANDIDATE", "0.90")
    monkeypatch.setenv("AGF_KW_REDUNDANT_MIN_KEPT", "20")
    monkeypatch.setenv("AGF_KW_REDUNDANT_SHARE", "0.6")


@pytest.fixture
def seed_catch(pg_conn):
    """Seed a keyword's kept catch: `kept` kept candidates, each with an embedding
    row. `crowding` is one value for all of them, or a per-row list (pass None in
    the list for a not-yet-scored embedding)."""
    ids = itertools.count(CAND_BASE)

    def _seed(keyword, *, kept, crowding):
        vals = list(crowding) if isinstance(crowding, list) else [crowding] * kept
        assert len(vals) == kept
        for cr in vals:
            cid = next(ids)
            pg_conn.execute(_CAND, {"id": cid, "pg": f"KWRED{cid}",
                                    "sid": str(cid), "kw": keyword})
            pg_conn.execute(_EMB, {"id": cid, "e": [1.0, 0.0], "cr": cr})
    return _seed


@pytest.fixture
def seed_kw(pg_conn, seed_catch):
    """A dynamic keyword plus its kept catch. Returns the keyword id."""
    ids = itertools.count(KW_BASE)

    def _seed(keyword, *, status="active", kept=0, crowding=0.95):
        kid = next(ids)
        pg_conn.execute(text(
            "INSERT INTO discovery.dynamic_keywords (id, keyword, source, kind, status) "
            "VALUES (:id, :kw, 'gem_copy', 'app', :st)"),
            {"id": kid, "kw": keyword, "st": status})
        if kept:
            seed_catch(keyword, kept=kept, crowding=crowding)
        return kid
    return _seed


def _statuses(conn, keywords):
    return dict(conn.execute(text(
        "SELECT keyword, status FROM discovery.dynamic_keywords "
        "WHERE keyword = ANY(:kws)"), {"kws": list(keywords)}).all())


# --- dynamic retire pass ---------------------------------------------------------------

def test_retire_redundant_dynamic_keyword(pg_conn, seed_kw):
    """The headline shape: a mostly-crowded catch retires, an uncrowded one and a
    too-small sample survive."""
    seed_kw("clone magnet", status="active", kept=25, crowding=0.95)
    seed_kw("fresh finder", status="active", kept=25, crowding=0.10)
    seed_kw("tiny sample", status="active", kept=5, crowding=0.95)

    retired = dk.retire_redundant_keywords(pg_conn)

    assert "clone magnet" in retired
    assert "fresh finder" not in retired
    assert "tiny sample" not in retired
    assert _statuses(pg_conn, ["clone magnet", "fresh finder", "tiny sample"]) == {
        "clone magnet": "retired", "fresh finder": "active", "tiny sample": "active"}


def test_retire_grades_resting_but_not_trials(pg_conn, seed_kw):
    """Resting keywords are in scope (a rested clone magnet must never wake up);
    trials are not — the promotion verdict already grades their catch."""
    seed_kw("rested magnet", status="resting", kept=25, crowding=0.95)
    seed_kw("trial magnet", status="trial", kept=25, crowding=0.95)

    retired = dk.retire_redundant_keywords(pg_conn)

    assert "rested magnet" in retired
    assert "trial magnet" not in retired
    assert _statuses(pg_conn, ["rested magnet", "trial magnet"]) == {
        "rested magnet": "retired", "trial magnet": "trial"}


def test_retire_degrades_open_on_null_crowding(pg_conn, seed_kw):
    """Unscored embeddings count in the DENOMINATOR only. 14 crowded of 25 rows is
    0.56 — under the bar — even though the 14 scored rows are 100% crowded."""
    seed_kw("half scored", status="active", kept=25,
            crowding=[0.95] * 14 + [None] * 11)

    assert "half scored" not in dk.retire_redundant_keywords(pg_conn)
    assert _statuses(pg_conn, ["half scored"]) == {"half scored": "active"}


def test_retire_thresholds_default(monkeypatch):
    monkeypatch.delenv("AGF_KW_REDUNDANT_MIN_KEPT", raising=False)
    monkeypatch.delenv("AGF_KW_REDUNDANT_SHARE", raising=False)
    assert dk.redundant_min_kept() == 20
    assert dk.redundant_share() == 0.6


# --- static suppression pass -----------------------------------------------------------

def test_static_pass_suppresses_redundant_keyword(pg_conn, seed_catch):
    seed_catch("install the app", kept=25, crowding=0.95)
    seed_catch("women running plan", kept=25, crowding=0.10)
    seed_catch("rare niche phrase", kept=5, crowding=0.95)
    kws = ["install the app", "women running plan", "rare niche phrase"]

    assert suppress_redundant_static_keywords(pg_conn, kws) == ["install the app"]

    row = pg_conn.execute(text(
        "SELECT reason, kept_n, crowded_share FROM discovery.keyword_suppressions "
        "WHERE keyword = 'install the app'")).one()
    assert row.reason == "redundant_catch"
    assert row.kept_n == 25
    assert row.crowded_share == pytest.approx(1.0, abs=1e-6)

    loaded = load_suppressed_keywords(pg_conn)
    assert "install the app" in loaded
    assert "women running plan" not in loaded
    assert "rare niche phrase" not in loaded


def test_static_pass_is_idempotent(pg_conn, seed_catch):
    """Re-running upserts the metrics and reports nothing NEWLY suppressed — the
    caller logs the return, so a daily pass must not re-announce old victims."""
    seed_catch("try the app free", kept=25, crowding=0.95)

    assert suppress_redundant_static_keywords(pg_conn, ["try the app free"]) == \
        ["try the app free"]
    assert suppress_redundant_static_keywords(pg_conn, ["try the app free"]) == []

    n = pg_conn.execute(text(
        "SELECT count(*) FROM discovery.keyword_suppressions "
        "WHERE keyword = 'try the app free'")).scalar_one()
    assert n == 1


def test_static_pass_degrades_open_on_null_crowding(pg_conn, seed_catch):
    seed_catch("half scored static", kept=25, crowding=[0.95] * 14 + [None] * 11)

    assert suppress_redundant_static_keywords(pg_conn, ["half scored static"]) == []
    assert "half scored static" not in load_suppressed_keywords(pg_conn)


def test_static_pass_no_keywords(pg_conn):
    assert suppress_redundant_static_keywords(pg_conn, []) == []
