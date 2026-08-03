"""Unit tests for the decoupled compute cycle (spec 2026-07-10).

Pure unit tests: every stage callable is spied via monkeypatch, so no real DB,
Gemini, or iTunes is touched. The headline contract is decoupling-safety — one
stage raising must NOT abort the others, and run_compute_cycle must never raise.
"""
from contextlib import contextmanager

import pytest

from agf.workers import compute_cycle


class _FakeConn:
    def commit(self):
        pass


class _FakeCtx:
    def __enter__(self):
        return _FakeConn()

    def __exit__(self, *a):
        return False


class _FakeEngine:
    """Stands in for a SQLAlchemy Engine: only .connect() is exercised (the
    embed stages open a raw connection to commit progress incrementally)."""

    def connect(self):
        return _FakeCtx()


@contextmanager
def _fake_begin(engine=None):
    yield _FakeConn()


@pytest.fixture
def spies(monkeypatch):
    """Patch every stage callable where compute_cycle reaches it and record the
    order in which the four stages fire."""
    order = []
    crowding_tables = []
    topics_calls = []
    topics_empty = {"value": False}

    def snapshot_spy(conn, week_start, only_ids=None):
        order.append("snapshot")
        return 7

    def embed_apps_spy(conn, commit_every=25):
        order.append("embed_apps")
        return {"embedded": 3, "skipped": 0}

    def score_all_users_spy(conn):
        order.append("score_all_users")
        return {"users": 1}

    def embed_candidates_spy(conn, itunes_client=None, commit_every=25, limit=200):
        order.append("embed_candidates")
        return {"embedded": 5, "skipped": 0}

    def refresh_crowding_spy(engine, *, table, **kwargs):
        crowding_tables.append(table)
        return 1

    def refresh_topics_spy(engine, **kwargs):
        topics_calls.append(kwargs)
        return {"rows": 9, "k": 3}

    # velocity + crowding + topics are imported at module scope in compute_cycle.
    monkeypatch.setattr(compute_cycle, "snapshot_advertiser_week", snapshot_spy)
    monkeypatch.setattr(compute_cycle, "refresh_crowding", refresh_crowding_spy)
    monkeypatch.setattr(compute_cycle, "refresh_topics", refresh_topics_spy)
    # frontend.app_topics is populated by default; the cold-start test flips it.
    monkeypatch.setattr(compute_cycle, "_topics_empty",
                        lambda engine: topics_empty["value"])
    monkeypatch.setattr(compute_cycle, "begin", _fake_begin)
    # the two embed stages + preference are imported lazily inside the function,
    # so patch them at their source module.
    monkeypatch.setattr("scripts.embed_apps.embed_apps", embed_apps_spy)
    monkeypatch.setattr("scripts.score_content_preferences.score_all_users",
                        score_all_users_spy)
    monkeypatch.setattr("agf.discovery.candidate_embed.embed_candidates",
                        embed_candidates_spy)

    # keep every stage gate enabled regardless of the ambient environment.
    for var in ("AGF_EMBED_APPS", "AGF_SCORE_PREFERENCES", "AGF_TRIAGE_PRIOR"):
        monkeypatch.setenv(var, "1")

    return {"order": order, "crowding_tables": crowding_tables,
            "topics_calls": topics_calls, "topics_empty": topics_empty,
            "monkeypatch": monkeypatch}


def test_runs_all_four_stages_in_order(spies):
    import datetime
    out = compute_cycle.run_compute_cycle(_FakeEngine(), http_client=object(),
                                          as_of=datetime.date(2026, 7, 10))
    assert spies["order"] == ["snapshot", "embed_apps", "score_all_users",
                              "embed_candidates"]
    assert isinstance(out, dict)


def test_one_stage_failure_does_not_abort_others(spies):
    import datetime

    def boom(conn, commit_every=25):
        spies["order"].append("embed_apps")
        raise RuntimeError("gemini 429")

    spies["monkeypatch"].setattr("scripts.embed_apps.embed_apps", boom)

    # must NOT raise — the whole point of the decouple is isolation.
    out = compute_cycle.run_compute_cycle(_FakeEngine(), http_client=object(),
                                          as_of=datetime.date(2026, 7, 10))

    # embeddings blew up, but preference + candidate-embed still ran.
    assert "score_all_users" in spies["order"]
    assert "embed_candidates" in spies["order"]
    assert isinstance(out, dict)
    # crowding for the app table must be skipped when the embed stage failed.
    assert "frontend.app_embeddings" not in spies["crowding_tables"]


def test_crowding_skipped_when_no_new_embeddings(spies):
    import datetime

    def embed_apps_zero(conn, commit_every=25):
        spies["order"].append("embed_apps")
        return {"embedded": 0, "skipped": 42}

    spies["monkeypatch"].setattr("scripts.embed_apps.embed_apps", embed_apps_zero)

    compute_cycle.run_compute_cycle(_FakeEngine(), http_client=object(),
                                    as_of=datetime.date(2026, 7, 10))

    # zero new app embeddings → no full crowding recompute for that table,
    # but the candidate embed spy reported 5 new rows → its crowding ran.
    assert "frontend.app_embeddings" not in spies["crowding_tables"]
    assert "discovery.candidate_embeddings" in spies["crowding_tables"]


def test_crowding_runs_when_embeddings_added(spies):
    import datetime
    compute_cycle.run_compute_cycle(_FakeEngine(), http_client=object(),
                                    as_of=datetime.date(2026, 7, 10))
    # both embed spies returned embedded>0 → both crowding refreshes fired.
    assert "frontend.app_embeddings" in spies["crowding_tables"]
    assert "discovery.candidate_embeddings" in spies["crowding_tables"]


def _embed_zero(spies):
    def embed_apps_zero(conn, commit_every=25):
        spies["order"].append("embed_apps")
        return {"embedded": 0, "skipped": 42}

    spies["monkeypatch"].setattr("scripts.embed_apps.embed_apps", embed_apps_zero)


def test_topics_refreshed_when_embeddings_added(spies):
    import datetime
    out = compute_cycle.run_compute_cycle(_FakeEngine(), http_client=object(),
                                          as_of=datetime.date(2026, 7, 10))
    # new app embeddings → topic assignments are stale, so k-means reruns.
    assert out["topics"] == {"rows": 9, "k": 3}
    assert len(spies["topics_calls"]) == 1


def test_topics_skipped_when_no_new_embeddings(spies):
    import datetime
    _embed_zero(spies)

    out = compute_cycle.run_compute_cycle(_FakeEngine(), http_client=object(),
                                          as_of=datetime.date(2026, 7, 10))

    # nothing new to cluster and app_topics already populated → no recompute.
    assert spies["topics_calls"] == []
    assert "topics" not in out


def test_topics_runs_on_cold_start_with_empty_table(spies):
    import datetime
    _embed_zero(spies)
    spies["topics_empty"]["value"] = True

    out = compute_cycle.run_compute_cycle(_FakeEngine(), http_client=object(),
                                          as_of=datetime.date(2026, 7, 10))

    # no new embeddings, but app_topics is empty (fresh migration 161) → backfill.
    assert out["topics"] == {"rows": 9, "k": 3}


def test_topic_failure_does_not_abort_later_stages(spies):
    import datetime

    def boom(engine, **kwargs):
        raise RuntimeError("kmeans blew up")

    spies["monkeypatch"].setattr(compute_cycle, "refresh_topics", boom)

    out = compute_cycle.run_compute_cycle(_FakeEngine(), http_client=object(),
                                          as_of=datetime.date(2026, 7, 10))

    assert "score_all_users" in spies["order"]
    assert "embed_candidates" in spies["order"]
    assert "topics" not in out
