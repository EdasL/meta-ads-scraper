"""The weekly generic-deep stage end-to-end against the FakeExtractor + test DB:
deep-paged candidates land tagged technique='generic_deep', collected ad copy mints
trial keywords with source='generic_deep', slots and trial-pool room are respected."""
import asyncio

from sqlalchemy import text

from agf.discovery.generic_deep import run_generic_deep_stage
from agf.discovery.taxonomy import TaxonomyEntry
from agf.parse import AdRecord
from tests.discovery.conftest import FakeExtractor

NOW = 1_749_000_000


def _ad(link, body, page_id, name="Generic Deep Test"):
    return AdRecord("a1", page_id, name, True, NOW - 5 * 86_400, None, 1,
                    "DOWNLOAD", "Install", link, body, page_like_count=500)


def _entries(deep_pages):
    return [TaxonomyEntry("gdtest phrase", "generic_deep",
                          "KEYWORD_EXACT_PHRASE", deep_pages)]


def _clear(eng):
    with eng.begin() as conn:
        conn.execute(text(
            "DELETE FROM discovery.candidates WHERE page_id LIKE 'GDTEST%'"))
        conn.execute(text(
            "DELETE FROM discovery.dynamic_keywords WHERE source = 'generic_deep'"))


def test_stage_persists_tagged_candidates_and_mints_from_copy(_migrated_engine):
    eng = _migrated_engine
    _clear(eng)
    try:
        # Two pages. The recurring phrase "night routine builder" spans TWO distinct
        # advertisers -> mine_phrases (min_advertisers=2) should surface it.
        pages = [
            [_ad("https://apps.apple.com/us/app/x/id90001",
                 "Build your night routine builder tonight", "GDTEST1")],
            [_ad("https://apps.apple.com/us/app/x/id90002",
                 "The night routine builder everyone loves", "GDTEST2")],
        ]
        ext = FakeExtractor().script_pages("gdtest phrase", pages)
        gate_calls = []

        def gate(phrases, limit):
            gate_calls.append((list(phrases), limit))
            return list(phrases)[:limit]

        # baseline the trial pool so room = mint_slots regardless of seeded trials
        with eng.connect() as conn:
            from agf.db.dynamic_keywords import trial_count
            room_cap = trial_count(conn) + 5
        stats = run_generic_deep_stage(
            eng, ext, deep_pages=10, kw_delay=0.0, mint_slots=5, max_trials=room_cap,
            min_advertisers=2, gate=gate, entries=_entries(10))
        # candidates landed with the lane's technique tag
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT technique FROM discovery.candidates "
                "WHERE page_id LIKE 'GDTEST%'")).all()
        assert len(rows) == 2 and all(r.technique == "generic_deep" for r in rows)
        # the cross-advertiser phrase was mined, gated, and added as a trial
        assert any("night routine builder" in m for m in stats["mined"])
        assert gate_calls, "the redundancy gate must be consulted"
        assert stats["trials_added"] >= 1
        assert stats["copy_rows"] == 2
        assert stats["sweep"]["page_log"][0]["keyword"] == "gdtest phrase"
        with eng.connect() as conn:
            src = conn.execute(text(
                "SELECT source FROM discovery.dynamic_keywords "
                "WHERE source = 'generic_deep' LIMIT 1")).scalar_one()
        assert src == "generic_deep"
    finally:
        _clear(eng)


def test_stage_respects_mint_slots_and_trial_room(_migrated_engine):
    eng = _migrated_engine
    _clear(eng)
    try:
        pages = [[_ad("https://apps.apple.com/us/app/x/id90003",
                      "unique alpha phrase here and unique beta phrase here",
                      "GDTEST3"),
                  _ad("https://apps.apple.com/us/app/x/id90004",
                      "unique alpha phrase here and unique beta phrase here",
                      "GDTEST4")]]
        ext = FakeExtractor().script_pages("gdtest phrase", pages)
        # max_trials equal to the current trial count -> room 0 -> nothing minted
        with eng.connect() as conn:
            from agf.db.dynamic_keywords import trial_count
            current = trial_count(conn)
        stats = run_generic_deep_stage(
            eng, ext, deep_pages=5, kw_delay=0.0, mint_slots=5, max_trials=current,
            min_advertisers=2, gate=None, entries=_entries(5))
        assert stats["mined"] == [] and stats["trials_added"] == 0
    finally:
        _clear(eng)


def test_generic_deep_due_gates_weekly(_migrated_engine):
    from agf.db.generic_deep_runs import (close_generic_deep_run,
                                          open_generic_deep_run)
    from agf.workers.main import generic_deep_due
    eng = _migrated_engine
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.generic_deep_runs"))
    with eng.connect() as conn:
        assert generic_deep_due(conn, dry_run=True, every_days=7) is False
        assert generic_deep_due(conn, dry_run=False, every_days=0) is False  # disabled
        assert generic_deep_due(conn, dry_run=False, every_days=7) is True
    with eng.begin() as conn:
        rid = open_generic_deep_run(conn)
        close_generic_deep_run(conn, rid, stats={})
    with eng.connect() as conn:
        assert generic_deep_due(conn, dry_run=False, every_days=7) is False
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.generic_deep_runs"))
