"""The worker's daily keyword flywheel — the gate, the trial lifecycle
(promote/retire policy in db/dynamic_keywords), and run_flywheel_stage end-to-end
with a fake extractor + injected corpus (zero network). Skips cleanly without
AGF_TEST_DB_URL."""
from datetime import date

from sqlalchemy import text

from agf.db.dynamic_keywords import (PROMOTE_MIN_NEW, TRIAL_SWEEPS,
                                     add_trial_keywords, close_flywheel_run,
                                     live_keywords, open_flywheel_run,
                                     record_keyword_sweep, tried_keywords,
                                     trial_count)
from agf.discovery.keyword_mining import mine_phrases
from agf.workers.main import (_run_flywheel_admission, flywheel_due,
                              run_flywheel_stage)

TODAY = date(2026, 6, 4)


def test_flywheel_gate_one_finished_run_per_day(pg_conn):
    assert flywheel_due(pg_conn, TODAY, dry_run=False) is True
    assert flywheel_due(pg_conn, TODAY, dry_run=True) is False
    run_id = open_flywheel_run(pg_conn)
    pg_conn.execute(text(
        "UPDATE discovery.flywheel_runs SET started_at=:d WHERE id=:i"),
        {"d": TODAY, "i": run_id})
    assert flywheel_due(pg_conn, TODAY, dry_run=False) is True   # open run: still due
    close_flywheel_run(pg_conn, run_id, stats={"mined": []})
    assert flywheel_due(pg_conn, TODAY, dry_run=False) is False


def test_trial_lifecycle_promote_and_retire(pg_conn):
    add_trial_keywords(pg_conn, ["fw earner", "fw dud"])
    ids = {r.keyword: r.id for r in live_keywords(pg_conn)
           if r.keyword in ("fw earner", "fw dud")}
    # earner: yields PROMOTE_MIN_NEW across the trial -> active at sweep TRIAL_SWEEPS
    for i in range(TRIAL_SWEEPS):
        st = record_keyword_sweep(pg_conn, ids["fw earner"],
                                  new_candidates=PROMOTE_MIN_NEW if i == 0 else 0)
    assert st == "active"
    # dud: zero yield through the whole trial -> retired
    for _ in range(TRIAL_SWEEPS):
        st = record_keyword_sweep(pg_conn, ids["fw dud"], new_candidates=0)
    assert st == "retired"
    # retired keywords leave the daily rotation but stay in the never-re-mint set
    live = {r.keyword for r in live_keywords(pg_conn)}
    assert "fw dud" not in live and "fw earner" in live
    assert "fw dud" in tried_keywords(pg_conn)
    # an active keyword keeps its status on later sweeps
    assert record_keyword_sweep(pg_conn, ids["fw earner"], new_candidates=1) == "active"


def test_duplicate_mint_is_a_noop(pg_conn):
    assert add_trial_keywords(pg_conn, ["fw once"]) == 1
    assert add_trial_keywords(pg_conn, ["fw once"]) == 0
    assert trial_count(pg_conn) >= 1


class _FakeAd:
    def __init__(self, page_id, store_id):
        self.page_id = page_id
        self.page_name = f"Page {page_id}"
        self.page_like_count = 900
        self.link_url = f"https://apps.apple.com/us/app/id{store_id}"
        self.cta_type = "INSTALL_MOBILE_APP"
        self.start_date = None


class _FakeExtractor:
    """One small app advertiser per searched keyword."""
    def __init__(self):
        self.searched = []

    async def search_page(self, *, query, cursor=None, **kw):
        self.searched.append(query)
        return [_FakeAd("FWPAGE1", "777000888")], None, False   # one page, no next


def test_run_flywheel_stage_mines_sweeps_and_records(_migrated_engine):
    eng = _migrated_engine

    def _clean():
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.candidates "
                              "WHERE page_id = 'FWPAGE1'"))
            conn.execute(text("DELETE FROM discovery.dynamic_keywords "
                              "WHERE keyword LIKE 'glute sculpt%'"))
    _clean()
    try:
        corpus = [(11, "Try the glute sculpt method that everyone shares."),
                  (12, "The glute sculpt method works in weeks.")]
        ex = _FakeExtractor()
        # min_advertisers=2 to match this 2-advertiser corpus; we're testing the
        # mine→sweep→record wiring, not the production default threshold.
        stats = run_flywheel_stage(eng, ex, max_pages=1, kw_delay=0,
                                   min_advertisers=2,
                                   corpus_fn=lambda conn, min_score: corpus)
        assert "glute sculpt method" in stats["mined"]
        assert "glute sculpt method" in ex.searched
        assert stats["candidates_new"] >= 1            # the fake ad landed in the pool
        with eng.connect() as conn:
            kw = conn.execute(text(
                "SELECT status, sweeps_used, new_candidates_total "
                "FROM discovery.dynamic_keywords WHERE keyword='glute sculpt method'"
            )).one()
            cand = conn.execute(text(
                "SELECT source_keyword, technique FROM discovery.candidates "
                "WHERE page_id='FWPAGE1'")).one()
        assert kw.status == "trial" and kw.sweeps_used == 1
        assert kw.new_candidates_total >= 1
        assert cand.technique == "flywheel"
    finally:
        _clean()


class _NoAdExtractor:
    """Returns no ads for any query — sweeps live keywords without creating candidates,
    so the gem_copy-path superset-dedup test leaves zero candidate/app side effects."""
    def __init__(self):
        self.searched = []

    async def search_page(self, *, query, cursor=None, **kw):
        self.searched.append(query)
        return [], None, False


def test_run_flywheel_stage_drops_token_superset_of_tried(_migrated_engine):
    # gem_copy path: a mined phrase that is an existing (tried) keyword + ONLY a noise
    # modifier word is dropped before it becomes a trial, even though the exact-match mine
    # exclusion and the retired-basin embedding gate both pass it. The two 2-gram sub-phrases
    # are already tried, so 'daily sculpt method' is the only mine survivor — and it is a
    # strict token-superset of 'sculpt method' whose only extra token ('daily') is a noise
    # modifier, so the subset-dedup must drop it (mined -> empty, no gate/embed side effects).
    eng = _migrated_engine

    def _clean():
        with eng.begin() as conn:
            conn.execute(text(
                "DELETE FROM discovery.dynamic_keywords "
                "WHERE keyword IN ('sculpt method','daily sculpt','daily sculpt method')"))
    _clean()
    try:
        with eng.begin() as conn:
            conn.execute(text(
                "INSERT INTO discovery.dynamic_keywords (keyword, source, kind, status) "
                "VALUES ('sculpt method','gem_copy','app','retired'),"
                "('daily sculpt','gem_copy','app','retired')"))
        corpus = [(21, "Try the daily sculpt method that everyone shares."),
                  (22, "The daily sculpt method works in weeks.")]
        stats = run_flywheel_stage(eng, _NoAdExtractor(), max_pages=1, kw_delay=0,
                                   min_advertisers=2,
                                   corpus_fn=lambda conn, min_score: corpus)
        assert "daily sculpt method" not in stats["mined"]   # noise-modifier superset -> dropped
        with eng.connect() as conn:
            n = conn.execute(text(
                "SELECT count(*) FROM discovery.dynamic_keywords "
                "WHERE keyword='daily sculpt method'")).scalar()
        assert n == 0
    finally:
        _clean()


def test_flywheel_gate_drops_keyword_near_retired_basin(_migrated_engine, monkeypatch):
    import agf.discovery.keyword_redundancy as kr
    from sqlalchemy import text
    eng = _migrated_engine

    def _clean():
        with eng.begin() as conn:
            conn.execute(text(
                "DELETE FROM discovery.dynamic_keywords WHERE keyword LIKE 'GATET%'"))
    _clean()
    try:
        with eng.begin() as conn:
            conn.execute(text(
                "INSERT INTO discovery.dynamic_keywords (keyword, source, kind, status, dry_streak)"
                " VALUES ('GATET retired basin','gem_copy','app','retired',0)"))

        # fake embed: the mined phrase 'GATET twin' is identical to the retired basin
        # vector; 'GATET novel' is orthogonal.
        vecs = {"GATET retired basin": [1.0, 0.0], "GATET twin": [1.0, 0.0],
                "GATET novel": [0.0, 1.0]}
        monkeypatch.setattr(kr, "embed_texts", lambda texts, **kw: [vecs[t] for t in texts])

        admitted = _run_flywheel_admission(eng, ["GATET twin", "GATET novel"])
        assert "GATET novel" in admitted
        assert "GATET twin" not in admitted
    finally:
        _clean()


class _CountingExtractor(_FakeExtractor):
    """Yields one candidate per keyword, like _FakeExtractor, but is instance-tracked
    so a K=2 run can prove disjoint shard coverage."""


class _BarrenPagingExtractor:
    """Endless empty SERP pages — exercises patience/depth stopping rules.
    pages_by_query counts how deep the sweep actually paged per keyword."""
    def __init__(self):
        self.pages_by_query = {}

    async def search_page(self, *, query, cursor=None, **kw):
        n = self.pages_by_query.get(query, 0) + 1
        self.pages_by_query[query] = n
        return [], f"c{n}", True                     # no ads, always a next page


def test_parallel_flywheel_covers_all_keywords_disjointly(_migrated_engine):
    eng = _migrated_engine
    kws = [f"pfw kw {i}" for i in range(6)]
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.dynamic_keywords "
                          "WHERE keyword LIKE 'pfw kw %'"))
        add_trial_keywords(conn, kws)
    ex1, ex2 = _CountingExtractor(), _CountingExtractor()
    try:
        stats = run_flywheel_stage(eng, [ex1, ex2], max_pages=1, kw_delay=0,
                                   corpus_fn=lambda conn, min_score: [])
        swept = ex1.searched + ex2.searched
        assert set(kws) <= set(swept)                      # every live kw swept
        assert not (set(ex1.searched) & set(ex2.searched)) # disjoint shards
        assert stats["keywords_swept"] >= 6
        with eng.connect() as conn:                        # verdicts recorded
            used = conn.execute(text(
                "SELECT min(sweeps_used) FROM discovery.dynamic_keywords "
                "WHERE keyword LIKE 'pfw kw %'")).scalar()
        assert used == 1
    finally:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.candidates "
                              "WHERE page_id = 'FWPAGE1'"))
            conn.execute(text("DELETE FROM discovery.dynamic_keywords "
                              "WHERE keyword LIKE 'pfw kw %'"))


def test_retested_keyword_first_sweep_is_forced_deep(_migrated_engine, monkeypatch):
    eng = _migrated_engine
    monkeypatch.setenv("AGF_SWEEP_PATIENCE_PAGES", "3")
    monkeypatch.setenv("AGF_SWEEP_MAX_DEPTH", "15")
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.dynamic_keywords "
                          "WHERE keyword LIKE 'deep rt %'"))
        add_trial_keywords(conn, ["deep rt retested", "deep rt normal"])
        conn.execute(text(
            "UPDATE discovery.dynamic_keywords SET retested_at=now(), sweeps_used=0 "
            "WHERE keyword='deep rt retested'"))
    ex = _BarrenPagingExtractor()
    try:
        run_flywheel_stage(eng, [ex], max_pages=1, kw_delay=0,
                           corpus_fn=lambda conn, min_score: [])
        # normal barren trial: patience window stops it early
        assert ex.pages_by_query["deep rt normal"] <= 4
        # retested first sweep: patience disabled up to the depth cap
        assert ex.pages_by_query["deep rt retested"] == 15
    finally:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.dynamic_keywords "
                              "WHERE keyword LIKE 'deep rt %'"))


def test_retest_admission_fills_room_after_mints(_migrated_engine):
    eng = _migrated_engine
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM discovery.dynamic_keywords "
                          "WHERE keyword LIKE 'rtadm %'"))
        _mk_retired_eng(conn, "rtadm shallow", "2026-06-20")
    ex = _FakeExtractor()
    try:
        stats = run_flywheel_stage(eng, [ex], max_pages=1, kw_delay=0,
                                   retest_per_pass=5,
                                   corpus_fn=lambda conn, min_score: [])
        assert stats["retested"] >= 1
        assert "rtadm shallow" in ex.searched          # re-admitted AND swept this pass
    finally:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.candidates "
                              "WHERE page_id = 'FWPAGE1'"))
            conn.execute(text("DELETE FROM discovery.dynamic_keywords "
                              "WHERE keyword LIKE 'rtadm %'"))


def _mk_retired_eng(conn, keyword, last_swept):
    add_trial_keywords(conn, [keyword])
    conn.execute(text(
        "UPDATE discovery.dynamic_keywords SET status='retired', sweeps_used=4, "
        "last_swept_at=:ls WHERE keyword=:kw"), {"ls": last_swept, "kw": keyword})


def test_mint_per_pass_caps_one_pass_minting(_migrated_engine):
    # Opening a large trial ceiling must NOT let one pass mint the miner's whole
    # tail (the 2026-07-10 182-junk-mint burst): mint_per_pass caps admissions
    # per pass independently of max_trials.
    eng = _migrated_engine

    def _clean():
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.dynamic_keywords "
                              "WHERE keyword LIKE 'quartz yoga%' "
                              "   OR keyword LIKE 'velvet cycling%'"))
    _clean()
    corpus = [(31, "the quartz yoga planner keeps you on track"),
              (32, "the quartz yoga planner everyone recommends"),
              (33, "try the velvet cycling tracker on your ride"),
              (34, "the velvet cycling tracker works offline")]
    # guard against a vacuous pass: this corpus must yield >=2 mineable phrases
    with eng.connect() as conn:
        tried = tried_keywords(conn)
    from agf.discovery.taxonomy import all_entries as _all
    baseline = mine_phrases(corpus, exclude={e.keyword for e in _all()} | tried,
                            limit=50, min_advertisers=2)
    assert len(baseline) >= 2
    try:
        stats = run_flywheel_stage(eng, _NoAdExtractor(), max_pages=1, kw_delay=0,
                                   min_advertisers=2, mint_per_pass=1,
                                   corpus_fn=lambda conn, min_score: corpus)
        assert len(stats["mined"]) == 1          # capped, despite room for more
    finally:
        _clean()
