"""Score every advertiser, write adtech.advertiser_scores, refresh the product
matview. score_all is conn-first (no commit); refresh uses its own autocommit
connection (REFRESH ... CONCURRENTLY cannot run in a transaction block)."""
from collections import defaultdict
from sqlalchemy import text
from agf.scoring.cohorts import pctile
from agf.scoring.features import build_features
from agf.scoring.gemscore import (scaling_velocity, under_radar, funnel_intent,
                                  demand, whitespace, gem_score, WINNER_AGE_CAP_DAYS)
from agf.scoring.vetoes import apply_vetoes
from agf.scoring.whitespace import load_niche_crowding

# GemScore >= threshold -> review queue. v2 recalibration: the two new axes
# (demand, whitespace) floor to 0.5 when data is missing, which multiplies every
# score by 0.5^(0.20+0.10) ~= 0.81 versus v1 — v1's 70 maps to ~57 on the v2
# scale. 60 keeps the review queue alive while demand history accrues, and is
# slightly stricter than the v1-equivalent bar.
ALERT_THRESHOLD = 60.0


def _eligible_advertiser_ids(conn):
    return [a for (a,) in conn.execute(text(
        "SELECT DISTINCT advertiser_id FROM public.ads "
        "WHERE advertiser_id IS NOT NULL ORDER BY advertiser_id")).all()]


def _values(members, attr):
    return [getattr(m, attr) for m in members if getattr(m, attr) is not None]


def _capped_age(days):
    # winner_age saturates at the cap so an incumbent's ancient ad can't dominate the
    # cohort normalize (see gemscore.WINNER_AGE_CAP_DAYS).
    return min(days, WINNER_AGE_CAP_DAYS)


def _sv_components(fv, members):
    # v3: running-days winner signal dominates; count_growth is a clamped (>=0) bonus.
    return {
        "winner_age": pctile(_capped_age(fv.max_longevity_days),
                             [_capped_age(v) for v in _values(members, "max_longevity_days")]),
        "winner_depth": pctile(fv.proven_winner_count, _values(members, "proven_winner_count")),
        "count_growth": pctile(fv.count_growth, _values(members, "count_growth")),
        "iteration": pctile(fv.iteration_rate, _values(members, "iteration_rate")),
    }


def _ur_components(fv, members):
    # higher raw (more ratings/followers/reach, older) => MORE known => LOWER UR
    def inv(attr, val):
        return None if val is None else 1.0 - pctile(val, _values(members, attr))
    return {
        "rating": inv("rating_count", fv.rating_count),
        "followers": inv("ig_followers", fv.ig_followers),
        "reach": inv("total_eu_reach", fv.total_eu_reach),
        "newness": inv("release_age_days", fv.release_age_days),
    }


def _fi_components(fv):
    return {
        "fingerprint": 1.0 if fv.has_funnel else 0.0,
        "cta": 1.0 if fv.install_cta else 0.0,
        "monetize": 1.0 if (fv.has_app or fv.has_funnel) else 0.0,
    }


def _d_components(fv, members):
    # store-traction velocity percentiles; None (no snapshots / iOS installs)
    # passes through so the axis renormalizes or floors
    def pc(attr, val):
        return None if val is None else pctile(val, _values(members, attr))
    return {
        "installs": pc("installs_velocity", fv.installs_velocity),
        "reviews": pc("review_velocity", fv.review_velocity),
    }


def score_all(conn, as_of, only_ids=None) -> int:
    """Build features, normalize within cohort, score, veto, write advertiser_scores.
    conn-first: no commit. Returns the number of advertisers scored.

    Production passes only_ids=None → scores EVERY advertiser, so cohort percentiles
    reflect the full population (correct). only_ids (an id allowlist) restricts both the
    scored set AND the cohort-normalization population to those ids — used by the
    backtest/synthetic harness so its corpus is scored against itself, isolated from any
    other rows sharing a cohort in the shared DB (score_all is otherwise global)."""
    aids = _eligible_advertiser_ids(conn)
    if only_ids is not None:
        keep = set(only_ids)
        aids = [a for a in aids if a in keep]
    fvs = [build_features(conn, aid, as_of) for aid in aids]
    by_cohort = defaultdict(list)
    for fv in fvs:
        by_cohort[fv.cohort].append(fv)
    # niche crowding is cross-advertiser, so it loads once per run (not per fv);
    # an advertiser without keyword lineage gets None -> whitespace floors
    niche_by_aid, niche_counts = load_niche_crowding(conn)

    n = 0
    for cohort, members in by_cohort.items():
        for fv in members:
            sv = scaling_velocity(_sv_components(fv, members))
            ur = under_radar(_ur_components(fv, members))
            fi = funnel_intent(_fi_components(fv))
            d = demand(_d_components(fv, members))
            niche = niche_by_aid.get(fv.advertiser_id)
            w = whitespace({"crowding": None if niche is None
                            else 1.0 - pctile(niche[1], niche_counts)})
            gs = gem_score(sv, ur, fi, d, w)
            veto = apply_vetoes(fv)
            alert = (gs >= ALERT_THRESHOLD) and not veto.vetoed
            conn.execute(text("""
                INSERT INTO adtech.advertiser_scores
                  (advertiser_id, cohort, gem_score, scaling_velocity, under_radar,
                   funnel_intent, demand, whitespace, niche_keyword,
                   niche_advertiser_count, installs_delta_28d, rating_count_delta_28d,
                   active_ad_count, max_longevity_days, alert,
                   vetoed, veto_reason, scored_at)
                VALUES (:adv, :coh, :gs, :sv, :ur, :fi, :d, :w, :nk, :nc, :idelta,
                        :rdelta, :aac, :mld, :alert, :vetoed, :reason, clock_timestamp())
                ON CONFLICT (advertiser_id) DO UPDATE SET
                  cohort=EXCLUDED.cohort, gem_score=EXCLUDED.gem_score,
                  scaling_velocity=EXCLUDED.scaling_velocity, under_radar=EXCLUDED.under_radar,
                  funnel_intent=EXCLUDED.funnel_intent, demand=EXCLUDED.demand,
                  whitespace=EXCLUDED.whitespace, niche_keyword=EXCLUDED.niche_keyword,
                  niche_advertiser_count=EXCLUDED.niche_advertiser_count,
                  installs_delta_28d=EXCLUDED.installs_delta_28d,
                  rating_count_delta_28d=EXCLUDED.rating_count_delta_28d,
                  active_ad_count=EXCLUDED.active_ad_count,
                  max_longevity_days=EXCLUDED.max_longevity_days, alert=EXCLUDED.alert,
                  vetoed=EXCLUDED.vetoed, veto_reason=EXCLUDED.veto_reason,
                  scored_at=clock_timestamp()
            """), {"adv": fv.advertiser_id, "coh": cohort, "gs": gs, "sv": sv,
                   "ur": ur, "fi": fi, "d": d, "w": w,
                   "nk": niche[0] if niche else None,
                   "nc": niche[1] if niche else None,
                   "idelta": fv.installs_delta_28d, "rdelta": fv.rating_count_delta_28d,
                   "aac": fv.active_ad_count,
                   "mld": fv.max_longevity_days, "alert": alert,
                   "vetoed": veto.vetoed, "reason": veto.reason})
            n += 1
    return n


def refresh_emerging_matview(engine) -> None:
    """REFRESH ... CONCURRENTLY on a dedicated autocommit connection (master-plan
    hard rule: cannot run inside a transaction block; needs the UNIQUE index)."""
    raw = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        raw.exec_driver_sql(
            "REFRESH MATERIALIZED VIEW CONCURRENTLY frontend.emerging_scaling_apps")
    finally:
        raw.close()


def run_scoring(engine, as_of) -> int:
    """Convenience orchestrator: score + recompute growth in one committed txn, then
    refresh. Growth must land before the refresh so the matview's LEFT JOIN picks it up."""
    from agf.db.engine import begin
    from agf.tracker.growth import compute_advertiser_growth
    with begin(engine) as conn:
        n = score_all(conn, as_of)
        compute_advertiser_growth(conn)
    refresh_emerging_matview(engine)
    return n
