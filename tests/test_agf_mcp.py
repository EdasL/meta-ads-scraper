"""Tests for the scraping-health MCP metrics.

DB-backed tests run against the test DB (AGF_TEST_DB_URL, :55433); they assert
shape/keys and read-only enforcement, not specific values. The idea-diversity
math is also checked on a deterministic synthetic embedding set with no DB.
"""

import os

import numpy as np
import pytest

from agf_mcp import db, metrics

TEST_URL = os.environ.get("AGF_TEST_DB_URL")
needs_db = pytest.mark.skipif(not TEST_URL, reason="AGF_TEST_DB_URL not set")


# --------------------------- read-only enforcement -------------------------- #

@needs_db
def test_readonly_blocks_write():
    # A non-temp write is rejected by the READ ONLY transaction. WHERE false
    # means it would be a no-op even if it ran, so this is safe.
    with pytest.raises(db.DBError, match="read-only|Database error"):
        db.run_rows(
            "UPDATE discovery.candidates SET status = status WHERE false",
            url=TEST_URL,
        )


@needs_db
def test_select_works():
    rows = db.run_rows("SELECT 1 AS one", url=TEST_URL)
    assert rows == [{"one": 1}]


# ------------------------------- tool shapes -------------------------------- #

@needs_db
def test_engine_pulse_shape():
    out = metrics.engine_pulse(url=TEST_URL)
    assert set(out) >= {"status", "newest_candidate", "triage_backlog_new",
                        "runs", "flags"}
    assert out["status"] in {"healthy", "wedged"}
    assert set(out["runs"]) == {"sweep_runs", "flywheel_runs", "funnel_sweep_runs"}


@needs_db
def test_discovery_rate_shape():
    out = metrics.discovery_rate(days=7, url=TEST_URL)
    assert out["window_days"] == 7
    assert isinstance(out["per_day"], list)
    assert set(out) >= {"per_day", "status_mix", "kept_rate",
                        "distinct_source_keywords_in_window"}


@needs_db
def test_discovery_rate_clamps_days():
    assert metrics.discovery_rate(days=99999, url=TEST_URL)["window_days"] == 120
    assert metrics.discovery_rate(days=0, url=TEST_URL)["window_days"] == 1


@needs_db
def test_idea_diversity_shape_or_empty():
    out = metrics.idea_diversity(url=TEST_URL)
    assert "n_embeddings" in out
    if out["n_embeddings"] >= 2:
        assert set(out) >= {"effective_concepts", "concentration",
                            "concept_groups", "novelty", "platform_split"}
        assert out["effective_concepts"] >= 1.0
        assert isinstance(out["concept_groups"], list)


@needs_db
def test_scraping_overview_shape():
    out = metrics.scraping_overview(url=TEST_URL)
    assert set(out) == {"engine_pulse", "discovery_rate", "idea_diversity"}


# --------------------------- pure math (no DB) ------------------------------ #

def test_effective_count_uniform_vs_skewed():
    uniform = np.array([10, 10, 10, 10])
    skewed = np.array([37, 1, 1, 1])
    assert metrics._effective_count(uniform) == pytest.approx(4.0, abs=1e-6)
    # one dominant concept -> effective count near 1
    assert metrics._effective_count(skewed) < 2.0


def test_concentration():
    c = metrics._concentration(np.array([5, 3, 2]))
    assert c["total"] == 10
    assert c["top1_share"] == 0.5


def test_idea_diversity_distinguishes_diverse_from_clones():
    """End-to-end on synthetic vectors via a fake DB layer (no real DB)."""
    rng = np.random.default_rng(0)

    def make_rows(spread):
        # `spread` large -> many distinct directions; small -> near-duplicates
        base = rng.normal(size=(6, 768))
        rows = []
        for i in range(120):
            center = base[i % 6] * spread
            v = center + rng.normal(scale=0.1, size=768)
            rows.append({
                "candidate_id": i, "embedding": v.astype("float32").tolist(),
                "store": "ios", "page_name": f"app{i}",
                "source_keyword": "kw", "first_seen_at": None, "is_gem": False,
            })
        return rows

    diverse = _run_with_rows(make_rows(5.0))
    clones = _run_with_rows(make_rows(0.05))
    assert diverse["effective_concepts"] > clones["effective_concepts"]


def _run_with_rows(rows):
    import agf_mcp.metrics as m
    orig = m.db.run_rows
    # coverage query returns one row; the embedding query returns the vectors
    m.db.run_rows = lambda sql, *a, **k: (
        [{"valid": len(rows), "total_kept_with_row": len(rows)}]
        if "FILTER" in sql else rows
    )
    try:
        return m.idea_diversity(url="fake")
    finally:
        m.db.run_rows = orig
