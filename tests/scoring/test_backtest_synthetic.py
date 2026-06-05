"""Synthetic-corpus property tests for GemScore priors (master-plan §6 "back-test").

Builds labeled advertiser trajectories in Postgres, runs the REAL score_all path
(pctile within cohort → gem_score → vetoes → advertiser_scores), then asserts on
RANK / THRESHOLD / VETO properties — never on exact scores.

Sentinel prefix: BT (page_ids BT01…BT20). Never collides with e2e P5* or API P7*.
Cohorts seeded: "Health & Fitness" (8 members) and "Education" (8 members).
All series ≥ 8 weeks so pctile is non-degenerate and seasonal_spike veto can fire.

Run:
    export AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf
    export PYTHONPATH=src
    .venv/bin/pytest tests/scoring/test_backtest_synthetic.py -q
"""
from datetime import date, datetime, timezone, timedelta

import pytest
from sqlalchemy import text

from agf.db import repo
from agf.db.engine import begin
from agf.parse import AdRecord
from agf.scoring.gemscore import gem_score
from agf.scoring.run_scoring import score_all, refresh_emerging_matview

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
AS_OF = date(2026, 6, 5)
NOW = int(datetime(2026, 6, 5, tzinfo=timezone.utc).timestamp())
DAY = 86_400
WEEK0 = date(2026, 2, 2)   # 18 weeks before AS_OF → enough headroom for 12-week series

# Sentinel page_ids — distinct from e2e (P5*) and API (P7*)
# fmt: off
SENTINELS = (
    # Health & Fitness cohort (8)
    "BT01", "BT02", "BT03", "BT04", "BT05", "BT06", "BT07", "BT08",
    # Education cohort (8)
    "BT11", "BT12", "BT13", "BT14", "BT15", "BT16", "BT17", "BT18",
)
# fmt: on

# ---------------------------------------------------------------------------
# Trajectory helpers
# ---------------------------------------------------------------------------

def _convex_rising(n=10, start=3, factor=1.6):
    """Exponentially accelerating ad-count series — the breakout pattern."""
    s = []
    v = float(start)
    for _ in range(n):
        s.append(max(1, int(v)))
        v *= factor
    return s


def _flat(n=10, level=80):
    """Flat high series — incumbent / flat advertiser."""
    return [level] * n


def _seasonal_no_trend(n=12, period=4):
    """Period-4 seasonal pattern, no underlying trend, ends on a peak."""
    base = [3, 1, 2, 6]  # matches e2e P5SPIKE pattern
    return (base * (n // period + 1))[:n]


def _seasonal_with_trend(n=12, period=4):
    """Period-4 seasonal + rising trend so seasonal_spike veto should NOT fire."""
    base = [3, 1, 2, 6]
    raw = (base * (n // period + 1))[:n]
    return [v + i * 2 for i, v in enumerate(raw)]


# ---------------------------------------------------------------------------
# Corpus specification
# genre, rating_count, release_date_str, is_emerging, longevity_days,
# series, reach, has_funnel, cta_type, parent_page_id (or None)
# ---------------------------------------------------------------------------

# "Health & Fitness" cohort — 8 members
HF_SPECS = {
    # --- true breakouts (2) ---
    "BT01": ("Health & Fitness", 50,        "2026-05-01", True,  35,
             _convex_rising(10), 100, True, "INSTALL_MOBILE_APP", None),
    "BT02": ("Health & Fitness", 70,        "2026-04-15", True,  51,
             _convex_rising(10, start=2, factor=1.8), 70, True, "INSTALL_MOBILE_APP", None),
    # --- incumbent (1) ---
    "BT03": ("Health & Fitness", 9_000_000, "2016-01-01", False, 500,
             _flat(10, 90), 12_000_000, False, "LEARN_MORE", None),
    # --- flat advertiser (1) ---
    "BT04": ("Health & Fitness", 5_000,     "2025-01-01", True,  150,
             _flat(10, 40), 20_000, False, "LEARN_MORE", None),
    # --- disguised incumbent (1) ---  parent_page_id will be set to BT03's adv id
    "BT05": ("Health & Fitness", 300,       "2026-03-01", True,  96,
             _convex_rising(10), 200, True, "INSTALL_MOBILE_APP", "BT03"),
    # --- relaunch (1) ---
    "BT06": ("Health & Fitness", 150_000,   "2026-02-01", True,  126,
             _convex_rising(10), 300, True, "INSTALL_MOBILE_APP", None),
    # --- seasonal_only (1) ---
    "BT07": ("Health & Fitness", 400,       "2025-10-01", True,  250,
             _seasonal_no_trend(12), 500, True, "INSTALL_MOBILE_APP", None),
    # --- seasonal_with_trend (1) ---
    "BT08": ("Health & Fitness", 350,       "2025-10-01", True,  250,
             _seasonal_with_trend(12), 400, True, "INSTALL_MOBILE_APP", None),
}

# "Education" cohort — 8 members
ED_SPECS = {
    # --- true breakout (2) ---
    "BT11": ("Education", 60,        "2026-05-01", True,  35,
             _convex_rising(10), 120, True, "INSTALL_MOBILE_APP", None),
    "BT12": ("Education", 65,        "2026-04-20", True,  45,
             _convex_rising(10, start=2, factor=1.8), 75, True, "INSTALL_MOBILE_APP", None),
    # --- incumbent (1) ---
    "BT13": ("Education", 7_000_000, "2015-06-01", False, 600,
             _flat(10, 85), 10_000_000, False, "LEARN_MORE", None),
    # --- flat advertiser (1) ---
    "BT14": ("Education", 4_000,     "2025-02-01", True,  145,
             _flat(10, 35), 15_000, False, "LEARN_MORE", None),
    # --- one_week_blip (1) — only 1 metric row → weeks_observed=1 < 2 ---
    "BT15": ("Education", 200,       "2026-05-10", True,  20,
             [5],  # single week
             200, True, "INSTALL_MOBILE_APP", None),
    # --- short_longevity (1) — longevity < 14d ---
    "BT16": ("Education", 100,       "2026-05-25", True,  10,   # 10d < 14d floor
             _convex_rising(8), 150, True, "INSTALL_MOBILE_APP", None),
    # --- disguised incumbent in Education (1) ---
    "BT17": ("Education", 250,       "2026-03-10", True,  87,
             _convex_rising(10), 180, True, "INSTALL_MOBILE_APP", "BT13"),
    # --- relaunch in Education (1) ---
    "BT18": ("Education", 120_000,   "2026-01-15", True,  141,
             _convex_rising(10), 220, True, "INSTALL_MOBILE_APP", None),
}

ALL_SPECS = {**HF_SPECS, **ED_SPECS}


# ---------------------------------------------------------------------------
# Seed / cleanup helpers
# ---------------------------------------------------------------------------

def _seed(conn):
    """Insert all corpus advertisers. Returns {page_id: advertiser_id}."""
    ids = {}
    for pid, spec in ALL_SPECS.items():
        genre, ratings, rel, emerging, longd, series, reach, funnel, cta, _parent = spec
        ar = AdRecord(
            f"{pid}-AD", pid, pid, True,
            NOW - longd * DAY,   # start_date epoch
            None, 5, cta, "Install",
            f"https://apps.apple.com/us/app/x/id{pid}", "body"
        )
        adv = repo.upsert_advertiser(conn, ar)
        app = repo.upsert_app(conn, {
            "store": "ios", "store_id": pid, "name": pid,
            "genre": genre, "rating_count": ratings,
            "release_date": rel, "is_emerging": emerging,
        })
        ad_id = repo.upsert_ad(conn, ar, adv, app)
        if funnel:
            repo.upsert_funnel(conn, ad_id,
                               f"https://{pid}.example",
                               f"https://{pid}.example/checkout",
                               "shot.png")
        for i, n in enumerate(series):
            repo.upsert_app_advertiser_metric(
                conn, adv, WEEK0 + timedelta(weeks=i), n, n, reach
            )
        ids[pid] = adv
    return ids


def _apply_parent_links(conn, ids):
    """Set parent_company_id for disguised incumbents (must run after ids known)."""
    for pid, spec in ALL_SPECS.items():
        _parent = spec[9]
        if _parent is not None:
            repo.upsert_advertiser_enrichment(
                conn, ids[pid], parent_company_id=ids[_parent]
            )


def _cleanup(engine):
    with begin(engine) as conn:
        adv_ids = conn.execute(
            text("SELECT id FROM public.advertisers WHERE page_id = ANY(:p)"),
            {"p": list(SENTINELS)},
        ).scalars().all()
        if adv_ids:
            conn.execute(text(
                "DELETE FROM adtech.advertiser_scores WHERE advertiser_id = ANY(:i)"
            ), {"i": adv_ids})
            conn.execute(text(
                "DELETE FROM adtech.app_advertiser_metrics WHERE advertiser_id = ANY(:i)"
            ), {"i": adv_ids})
            conn.execute(text(
                "DELETE FROM adtech.ad_metrics m USING public.ads a "
                "WHERE m.ad_id=a.id AND a.advertiser_id = ANY(:i)"
            ), {"i": adv_ids})
            conn.execute(text(
                "DELETE FROM public.funnels f USING public.ads a "
                "WHERE f.ad_id=a.id AND a.advertiser_id = ANY(:i)"
            ), {"i": adv_ids})
            conn.execute(text(
                "DELETE FROM public.creatives c USING public.ads a "
                "WHERE c.ad_id=a.id AND a.advertiser_id = ANY(:i)"
            ), {"i": adv_ids})
            conn.execute(text(
                "DELETE FROM public.ads WHERE advertiser_id = ANY(:i)"
            ), {"i": adv_ids})
            conn.execute(text(
                "DELETE FROM public.advertisers WHERE id = ANY(:i)"
            ), {"i": adv_ids})
        conn.execute(
            text("DELETE FROM public.apps WHERE store_id = ANY(:p)"),
            {"p": list(SENTINELS)},
        )
    refresh_emerging_matview(engine)


# ---------------------------------------------------------------------------
# Pytest fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def scored(_migrated_engine):
    """Seed corpus, score, yield (engine, advertiser_ids, scores, rows). Cleanup."""
    eng = _migrated_engine
    _cleanup(eng)
    try:
        with begin(eng) as conn:
            ids = _seed(conn)
            _apply_parent_links(conn, ids)
            score_all(conn, AS_OF)
        refresh_emerging_matview(eng)

        with eng.connect() as conn:
            score_rows = {
                r.advertiser_id: r
                for r in conn.execute(
                    text(
                        "SELECT advertiser_id, gem_score, vetoed, veto_reason, alert "
                        "FROM adtech.advertiser_scores WHERE advertiser_id = ANY(:i)"
                    ),
                    {"i": list(ids.values())},
                ).all()
            }
        yield ids, score_rows
    finally:
        _cleanup(eng)


# ---------------------------------------------------------------------------
# Helper: look up a score row by page_id
# ---------------------------------------------------------------------------

def _s(scored, pid):
    ids, rows = scored
    return rows[ids[pid]]


# ---------------------------------------------------------------------------
# 1. True-breakout asserts
# ---------------------------------------------------------------------------

def test_breakout_alerts_and_high_score(scored):
    """Both true-breakouts in each cohort must alert with gem_score >= 70."""
    for pid in ("BT01", "BT02", "BT11", "BT12"):
        r = _s(scored, pid)
        assert r.alert is True, f"{pid}: expected alert=True, got {r.gem_score:.1f} vetoed={r.vetoed}/{r.veto_reason}"
        assert r.gem_score >= 70, f"{pid}: gem_score={r.gem_score:.1f} < 70"


# ---------------------------------------------------------------------------
# 2. Breakout > incumbent ordering
# ---------------------------------------------------------------------------

def test_breakout_scores_higher_than_incumbent(scored):
    for breakout, incumbent in [("BT01", "BT03"), ("BT11", "BT13")]:
        gs_b = _s(scored, breakout).gem_score
        gs_i = _s(scored, incumbent).gem_score
        assert gs_b > gs_i, (
            f"{breakout}({gs_b:.1f}) must beat {incumbent}({gs_i:.1f})"
        )


# ---------------------------------------------------------------------------
# 3. Incumbent does NOT alert
# ---------------------------------------------------------------------------

def test_incumbent_does_not_alert(scored):
    for pid in ("BT03", "BT13"):
        assert _s(scored, pid).alert is False, f"{pid} incumbent should not alert"


# ---------------------------------------------------------------------------
# 4. Flat advertiser does NOT alert
# ---------------------------------------------------------------------------

def test_flat_advertiser_does_not_alert(scored):
    for pid in ("BT04", "BT14"):
        assert _s(scored, pid).alert is False, f"{pid} flat advertiser should not alert"


# ---------------------------------------------------------------------------
# 5. Seasonal-only → vetoed with reason 'seasonal_spike'
# ---------------------------------------------------------------------------

def test_seasonal_only_vetoed(scored):
    r = _s(scored, "BT07")
    assert r.vetoed is True, f"BT07 seasonal_only: expected vetoed, got gem_score={r.gem_score:.1f}"
    assert r.veto_reason == "seasonal_spike", f"BT07 veto_reason={r.veto_reason!r}"
    assert r.alert is False


# ---------------------------------------------------------------------------
# 6. Seasonal-with-trend → NOT vetoed for seasonal_spike
#    (may still fail other vetoes, but reason must NOT be seasonal_spike)
# ---------------------------------------------------------------------------

def test_seasonal_with_trend_not_seasonal_spike_vetoed(scored):
    r = _s(scored, "BT08")
    assert r.veto_reason != "seasonal_spike", (
        f"BT08 seasonal_with_trend should not be vetoed for seasonal_spike; "
        f"got veto_reason={r.veto_reason!r}"
    )


# ---------------------------------------------------------------------------
# 7. Disguised incumbent → vetoed 'disguised_incumbent'
# ---------------------------------------------------------------------------

def test_disguised_incumbent_vetoed(scored):
    for pid in ("BT05", "BT17"):
        r = _s(scored, pid)
        assert r.vetoed is True, f"{pid}: expected vetoed"
        assert r.veto_reason == "disguised_incumbent", f"{pid}: veto_reason={r.veto_reason!r}"
        assert r.alert is False


# ---------------------------------------------------------------------------
# 8. Relaunch → vetoed 'relaunch'
# ---------------------------------------------------------------------------

def test_relaunch_vetoed(scored):
    for pid in ("BT06", "BT18"):
        r = _s(scored, pid)
        assert r.vetoed is True, f"{pid}: expected vetoed"
        assert r.veto_reason == "relaunch", f"{pid}: veto_reason={r.veto_reason!r}"
        assert r.alert is False


# ---------------------------------------------------------------------------
# 9. One-week blip → vetoed 'insufficient_persistence'
# ---------------------------------------------------------------------------

def test_one_week_blip_vetoed(scored):
    r = _s(scored, "BT15")
    assert r.vetoed is True, "BT15 one_week_blip: expected vetoed"
    assert r.veto_reason == "insufficient_persistence", (
        f"BT15 veto_reason={r.veto_reason!r}"
    )
    assert r.alert is False


# ---------------------------------------------------------------------------
# 10. Short longevity → vetoed 'longevity_below_floor'
# ---------------------------------------------------------------------------

def test_short_longevity_vetoed(scored):
    r = _s(scored, "BT16")
    assert r.vetoed is True, "BT16 short_longevity: expected vetoed"
    assert r.veto_reason == "longevity_below_floor", (
        f"BT16 veto_reason={r.veto_reason!r}"
    )
    assert r.alert is False


# ---------------------------------------------------------------------------
# 11. Invariant: gem_score is non-decreasing in a given axis (direct unit test)
# ---------------------------------------------------------------------------

def test_gem_score_monotone_in_sv():
    """Higher SV → higher gem_score when UR and FI are held constant."""
    ur, fi = 0.5, 0.8
    scores = [gem_score(sv, ur, fi) for sv in [0.0, 0.25, 0.5, 0.75, 1.0]]
    for i in range(len(scores) - 1):
        assert scores[i] <= scores[i + 1], (
            f"gem_score not non-decreasing in SV at index {i}: {scores}"
        )


def test_gem_score_monotone_in_ur():
    sv, fi = 0.7, 0.8
    scores = [gem_score(sv, ur, fi) for ur in [0.0, 0.25, 0.5, 0.75, 1.0]]
    for i in range(len(scores) - 1):
        assert scores[i] <= scores[i + 1]


def test_gem_score_monotone_in_fi():
    sv, ur = 0.7, 0.6
    scores = [gem_score(sv, ur, fi) for fi in [0.0, 0.25, 0.5, 0.75, 1.0]]
    for i in range(len(scores) - 1):
        assert scores[i] <= scores[i + 1]


# ---------------------------------------------------------------------------
# 12. Invariant: None axis imputed to floor (0.05), present-zero axis → near-zero
# ---------------------------------------------------------------------------

def test_none_axis_uses_floor_not_zero():
    """A None axis imputes to floor=0.05, so score > 0; a present 0.0 gates it."""
    score_none_sv = gem_score(None, 0.8, 0.8)   # SV missing → floor
    score_zero_sv = gem_score(0.0, 0.8, 0.8)    # SV present but zero
    # None-imputed score uses floor=0.05 → score ≈ 100 * 0.05^0.5 * 0.8^0.35 * 0.8^0.15
    assert score_none_sv > 0, "None SV should not zero the score"
    assert score_none_sv > score_zero_sv, (
        "None SV (floor) should score higher than present 0.0 SV (collapses to 0)"
    )
    # A genuine present-zero axis should produce exactly 0
    assert score_zero_sv == 0.0, f"Present SV=0 should give score=0, got {score_zero_sv}"
