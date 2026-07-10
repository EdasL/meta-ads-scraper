"""Trial promotion counts only UNCROWDED yield (crowding penalty §D).

The promote metric tightens from `new_candidates_total >= PROMOTE_MIN_NEW` to
`new_candidates_total - crowded_candidate_count >= PROMOTE_MIN_NEW`; the bar is
unchanged. A trial whose finds are all embedding-space clones no longer promotes.
Degrade OPEN: a candidate with no embedding or NULL crowding is NOT crowded, so a
keyword with no populated crowding behaves exactly as it does today.

Shared agf_it DB: every row is scoped to a high sentinel id range / FWCROWD keyword
sentinels, and pg_conn rolls the whole thing back."""
from sqlalchemy import text

from agf.db import dynamic_keywords as dk

CAND_BASE = 9_300_000_000          # sentinel candidate ids (candidates + embeddings)
DIM = 4


def _status(conn, keyword):
    return conn.execute(text(
        "SELECT status FROM discovery.dynamic_keywords WHERE keyword = :k"),
        {"k": keyword}).scalar()


def _kid(conn, keyword):
    return conn.execute(text(
        "SELECT id FROM discovery.dynamic_keywords WHERE keyword = :k"),
        {"k": keyword}).scalar()


def _seed_candidates(conn, keyword, *, offset, crowdings):
    """Seed one kept candidate per entry in `crowdings` for `keyword`. Each entry is
    the crowding to store: a float, None (NULL crowding row), or "no-emb" (candidate
    with no embedding row at all). Ids are sentinel-scoped by `offset`."""
    cand = text("INSERT INTO discovery.candidates "
                "(id, page_id, page_name, store, store_id, status, source_keyword) "
                "VALUES (:id, :pg, :nm, 'ios', :sid, 'kept', :kw)")
    emb = text("INSERT INTO discovery.candidate_embeddings "
               "(candidate_id, embedding, content_hash, dim, model, crowding) "
               "VALUES (:id, :e, 'sentinel', :d, 'test', :cr)")
    vec = [0.5] * DIM
    for i, cr in enumerate(crowdings):
        cid = CAND_BASE + offset + i
        conn.execute(cand, {"id": cid, "pg": f"pg{cid}", "nm": f"c{cid}",
                            "sid": f"s{cid}", "kw": keyword})
        if cr != "no-emb":
            conn.execute(emb, {"id": cid, "e": vec, "d": DIM, "cr": cr})


def _trial_at_budget(conn, keyword, *, new_total):
    """A trial one sweep short of its budget with `new_total` recorded finds."""
    dk.add_trial_keywords(conn, [keyword])
    conn.execute(text(
        "UPDATE discovery.dynamic_keywords SET sweeps_used = 3, new_candidates_total = :n "
        "WHERE keyword = :k"), {"n": new_total, "k": keyword})
    return _kid(conn, keyword)


def test_all_crowded_trial_not_promoted(pg_conn, monkeypatch):
    # 5 finds, all embedding-space clones (crowding >= tail) -> uncrowded yield 0 -> retired.
    monkeypatch.setenv("AGF_FLYWHEEL_TRIAL_SWEEPS", "4")
    monkeypatch.delenv("AGF_FLYWHEEL_PROMOTE_MIN_NEW", raising=False)   # default 1
    monkeypatch.delenv("AGF_CROWDING_TAIL", raising=False)             # default 0.80
    kw = "FWCROWD allcrowd"
    _seed_candidates(pg_conn, kw, offset=0, crowdings=[0.9] * 5)
    kid = _trial_at_budget(pg_conn, kw, new_total=5)

    assert dk.record_keyword_sweep(pg_conn, kid, new_candidates=0) == "retired"
    assert _status(pg_conn, kw) == "retired"


def test_zero_crowded_trial_promoted(pg_conn, monkeypatch):
    # 5 finds, none crowded -> full uncrowded yield -> promoted.
    monkeypatch.setenv("AGF_FLYWHEEL_TRIAL_SWEEPS", "4")
    monkeypatch.delenv("AGF_FLYWHEEL_PROMOTE_MIN_NEW", raising=False)
    monkeypatch.delenv("AGF_CROWDING_TAIL", raising=False)
    kw = "FWCROWD uncrowded"
    _seed_candidates(pg_conn, kw, offset=100, crowdings=[0.1] * 5)
    kid = _trial_at_budget(pg_conn, kw, new_total=5)

    assert dk.record_keyword_sweep(pg_conn, kid, new_candidates=0) == "active"
    assert _status(pg_conn, kw) == "active"


def test_no_embeddings_trial_promoted_degrade_open(pg_conn, monkeypatch):
    # Degrade OPEN: no embedding + NULL crowding rows count as NOT crowded, so a keyword
    # with no populated crowding promotes exactly as it does today.
    monkeypatch.setenv("AGF_FLYWHEEL_TRIAL_SWEEPS", "4")
    monkeypatch.delenv("AGF_FLYWHEEL_PROMOTE_MIN_NEW", raising=False)
    monkeypatch.delenv("AGF_CROWDING_TAIL", raising=False)
    kw = "FWCROWD degrade"
    _seed_candidates(pg_conn, kw, offset=200,
                     crowdings=["no-emb", "no-emb", "no-emb", None, None])
    kid = _trial_at_budget(pg_conn, kw, new_total=5)

    assert dk.record_keyword_sweep(pg_conn, kid, new_candidates=0) == "active"
    assert _status(pg_conn, kw) == "active"


def test_crowded_candidate_count_counts_only_tail(pg_conn, monkeypatch):
    monkeypatch.delenv("AGF_CROWDING_TAIL", raising=False)
    kw = "FWCROWD counting"
    # 3 crowded (>=0.80), 1 just under, 1 NULL, 1 with no embedding at all.
    _seed_candidates(pg_conn, kw, offset=300,
                     crowdings=[0.95, 0.80, 0.81, 0.79, None, "no-emb"])
    assert dk.crowded_candidate_count(pg_conn, kw, tail=dk.crowding_tail()) == 3


def test_crowding_tail_reads_env(monkeypatch):
    monkeypatch.setenv("AGF_CROWDING_TAIL", "0.5")
    assert dk.crowding_tail() == 0.5
    monkeypatch.delenv("AGF_CROWDING_TAIL", raising=False)
    assert dk.crowding_tail() == 0.80


def test_apply_verdicts_uses_uncrowded_yield(pg_conn, monkeypatch):
    # The batch path applies the same tightened metric: an all-crowded trial retires,
    # an uncrowded one promotes, and a no-embedding one degrades open (promotes).
    monkeypatch.setenv("AGF_FLYWHEEL_TRIAL_SWEEPS", "2")
    monkeypatch.delenv("AGF_FLYWHEEL_PROMOTE_MIN_NEW", raising=False)   # default 1
    monkeypatch.delenv("AGF_CROWDING_TAIL", raising=False)

    crowded_kw, clean_kw, bare_kw = ("FWCROWD batch-crowd", "FWCROWD batch-clean",
                                     "FWCROWD batch-bare")
    _seed_candidates(pg_conn, crowded_kw, offset=400, crowdings=[0.9] * 5)
    _seed_candidates(pg_conn, clean_kw, offset=500, crowdings=[0.1] * 5)
    _seed_candidates(pg_conn, bare_kw, offset=600, crowdings=["no-emb"] * 5)
    for kw in (crowded_kw, clean_kw, bare_kw):
        dk.add_trial_keywords(pg_conn, [kw])
        pg_conn.execute(text(
            "UPDATE discovery.dynamic_keywords SET sweeps_used = 2, new_candidates_total = 5 "
            "WHERE keyword = :k"), {"k": kw})

    dk.apply_verdicts(pg_conn, kind="app")

    assert _status(pg_conn, crowded_kw) == "retired"
    assert _status(pg_conn, clean_kw) == "active"
    assert _status(pg_conn, bare_kw) == "active"
