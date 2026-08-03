"""Enforcement of discovery.keyword_suppressions (spec 2026-08-03): the sweep's
DEFAULT entry branches and the generic-deep lane must skip suppressed keywords,
and the generic-deep pass must run the weekly redundancy grading first.

Explicit-entries callers of run_sweep are deliberately NOT filtered (the hunt and
flywheel lanes pass their own keywords); only the taxonomy defaults are.

Suppression rows are COMMITTED here — drop_suppressed_entries opens its own
connection off the engine, so a rolled-back pg_conn would be invisible to it.
Every fixture deletes its own row in teardown."""
import asyncio
from dataclasses import dataclass, field

import pytest
from sqlalchemy import text

from agf.discovery.generic_deep import run_generic_deep_stage
from agf.discovery.sweep import run_parallel_sweep, run_sweep
from agf.discovery.taxonomy import TaxonomyEntry, sweep_entries
from tests.discovery.conftest import FakeExtractor

# Two real sweep keywords: the first gets suppressed, the second must survive.
SUPPRESSED_KW = sweep_entries()[0].keyword
KEPT_KW = sweep_entries()[1].keyword


@dataclass
class RecordingExtractor(FakeExtractor):
    """FakeExtractor that records every keyword search_page was asked for."""
    searched_keywords: list = field(default_factory=list)

    async def search_page(self, *, query="", **kwargs):
        self.searched_keywords.append(query)
        return await super().search_page(query=query, **kwargs)


@pytest.fixture
def suppress(_migrated_engine):
    """Commit a keyword_suppressions row; remove it again in teardown."""
    added: list[str] = []

    def _suppress(keyword, *, kept_n=25, crowded_share=0.8):
        with _migrated_engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO discovery.keyword_suppressions "
                "(keyword, reason, kept_n, crowded_share) "
                "VALUES (:kw, 'redundant_catch', :n, :s) "
                "ON CONFLICT (keyword) DO NOTHING"),
                {"kw": keyword, "n": kept_n, "s": crowded_share})
        added.append(keyword)
    yield _suppress
    if added:
        with _migrated_engine.begin() as conn:
            conn.execute(text(
                "DELETE FROM discovery.keyword_suppressions "
                "WHERE keyword = ANY(:kws)"), {"kws": added})


# --- sweep default-entry branches ------------------------------------------------------

def test_run_sweep_default_entries_skip_suppressed(_migrated_engine, suppress):
    suppress(SUPPRESSED_KW)
    ext = RecordingExtractor()
    # entries=None takes the default (taxonomy) branch; `keywords` narrows the
    # already-filtered list so the test sweeps two keywords, not all ~300.
    asyncio.run(run_sweep(_migrated_engine, ext, keywords=[SUPPRESSED_KW, KEPT_KW],
                          max_pages=1, kw_delay=0, sleep=lambda s: None))

    assert SUPPRESSED_KW not in ext.searched_keywords
    assert KEPT_KW in ext.searched_keywords


def test_run_sweep_explicit_entries_are_not_filtered(_migrated_engine, suppress):
    """A caller that names its own entries owns them — suppression is a default-
    taxonomy policy, not a global ban."""
    suppress(SUPPRESSED_KW)
    ext = RecordingExtractor()
    entries = [TaxonomyEntry(SUPPRESSED_KW, "niche_term", "KEYWORD_UNORDERED", 1)]
    asyncio.run(run_sweep(_migrated_engine, ext, entries=entries, max_pages=1,
                          kw_delay=0, sleep=lambda s: None))

    assert SUPPRESSED_KW in ext.searched_keywords


def test_run_parallel_sweep_default_entries_skip_suppressed(_migrated_engine, suppress):
    suppress(SUPPRESSED_KW)
    ext = RecordingExtractor()
    run_parallel_sweep(_migrated_engine, [ext], keywords=[SUPPRESSED_KW, KEPT_KW],
                       max_pages=1, kw_delay=0)

    assert SUPPRESSED_KW not in ext.searched_keywords
    assert KEPT_KW in ext.searched_keywords


# --- generic-deep lane -----------------------------------------------------------------

def test_generic_deep_stage_runs_grading_and_filters(_migrated_engine, suppress):
    """The stage grades redundancy first, then drops suppressed entries — including
    test-injected ones, so the lane can never re-page a mined-out phrase."""
    suppress("gdsuppressed phrase")
    ext = RecordingExtractor()
    entries = [
        TaxonomyEntry("gdsuppressed phrase", "generic_deep", "KEYWORD_EXACT_PHRASE", 1),
        TaxonomyEntry("gdkept phrase", "generic_deep", "KEYWORD_EXACT_PHRASE", 1),
    ]
    out = run_generic_deep_stage(_migrated_engine, ext, deep_pages=1, kw_delay=0.0,
                                 entries=entries)

    assert "gdsuppressed phrase" not in ext.searched_keywords
    assert "gdkept phrase" in ext.searched_keywords
    assert isinstance(out["retired_redundant"], list)
    assert isinstance(out["suppressed"], list)


def test_generic_deep_grading_failure_does_not_stop_the_pass(_migrated_engine,
                                                             monkeypatch):
    """Grading is fail-soft: a broken pass logs and the harvest still runs."""
    def _boom(conn):
        raise RuntimeError("grading down")

    monkeypatch.setattr("agf.db.dynamic_keywords.retire_redundant_keywords", _boom)
    ext = RecordingExtractor()
    entries = [TaxonomyEntry("gdkept phrase", "generic_deep",
                             "KEYWORD_EXACT_PHRASE", 1)]
    out = run_generic_deep_stage(_migrated_engine, ext, deep_pages=1, kw_delay=0.0,
                                 entries=entries)

    assert "gdkept phrase" in ext.searched_keywords
    assert out["retired_redundant"] == [] and out["suppressed"] == []
