"""Derivative velocity features (master-plan §6 P5-T4/T5).
Primitives operate on an ordered weekly series (oldest -> newest)."""
import math

_EPS = 1.0  # log smoothing so a 0 -> N week doesn't blow up


def log_growth_wow(series) -> float:
    """Latest week-over-week log growth. Flat or <2 points -> 0.0."""
    s = list(series)
    if len(s) < 2:
        return 0.0
    return math.log((s[-1] + _EPS) / (s[-2] + _EPS))


def acceleration(series) -> float:
    """Change in WoW log-growth (2nd derivative). Convex>0, concave<0, flat/<3 ->0."""
    s = list(series)
    if len(s) < 3:
        return 0.0
    g_prev = math.log((s[-2] + _EPS) / (s[-3] + _EPS))
    g_last = math.log((s[-1] + _EPS) / (s[-2] + _EPS))
    return g_last - g_prev


def ewma(series, alpha: float = 0.5) -> float:
    """Exponentially weighted moving average (latest value). Empty -> 0.0."""
    s = list(series)
    if not s:
        return 0.0
    acc = float(s[0])
    for x in s[1:]:
        acc = alpha * float(x) + (1 - alpha) * acc
    return acc


from dataclasses import dataclass
from datetime import datetime, timezone
from sqlalchemy import text
from agf.scale import longevity_days
from agf.scoring.cohorts import cohort_key

_MONETIZE_CTAS = {"INSTALL_MOBILE_APP", "SHOP_NOW", "SUBSCRIBE", "SIGN_UP",
                  "GET_OFFER", "ORDER_NOW", "DOWNLOAD", "GET_QUOTE"}
LONGEVITY_FLOOR_DAYS = 14


@dataclass(frozen=True)
class FeatureVector:
    advertiser_id: int
    cohort: str
    page_name: str | None
    app_id: int | None
    app_name: str | None
    # scaling-velocity inputs
    wow_log_growth: float
    accel: float
    ewma_velocity: float
    max_longevity_days: int
    longevity_share: float
    active_ad_count: int
    collation_total: int
    velocity_series: tuple
    # under-radar inputs
    rating_count: int | None
    ig_followers: int | None
    release_age_days: int | None
    total_eu_reach: int | None
    # funnel-intent inputs
    has_funnel: bool
    install_cta: bool
    has_app: bool
    # veto inputs
    is_emerging: bool
    parent_company_id: int | None
    weeks_observed: int


def _as_of_epoch(as_of) -> int:
    return int(datetime(as_of.year, as_of.month, as_of.day,
                        tzinfo=timezone.utc).timestamp())


def build_features(conn, advertiser_id: int, as_of) -> FeatureVector:
    """Assemble the per-advertiser FeatureVector as of `as_of` (a date)."""
    now_epoch = _as_of_epoch(as_of)

    series = [int(n) for (n,) in conn.execute(text(
        "SELECT active_ad_count FROM adtech.app_advertiser_metrics "
        "WHERE advertiser_id=:a AND week_start <= :d ORDER BY week_start"),
        {"a": advertiser_id, "d": as_of}).all()]
    reach_latest = conn.execute(text(
        "SELECT total_eu_reach FROM adtech.app_advertiser_metrics "
        "WHERE advertiser_id=:a AND week_start <= :d ORDER BY week_start DESC LIMIT 1"),
        {"a": advertiser_id, "d": as_of}).scalar()

    ad_rows = conn.execute(text(
        "SELECT is_active, cta_type, collation_count, app_id, "
        "       EXTRACT(EPOCH FROM start_date)::bigint AS start_epoch "
        "FROM public.ads WHERE advertiser_id=:a"), {"a": advertiser_id}).all()
    active = [r for r in ad_rows if r.is_active]
    longs = [longevity_days(r.start_epoch, now_epoch) for r in active if r.start_epoch]
    max_long = max(longs) if longs else 0
    long_share = (sum(1 for d in longs if d >= LONGEVITY_FLOOR_DAYS) / len(longs)
                  if longs else 0.0)
    collation_total = sum(int(r.collation_count or 0) for r in active)
    install_cta = any((r.cta_type or "") in _MONETIZE_CTAS for r in active)
    has_app = any(r.app_id is not None for r in ad_rows)

    adv = conn.execute(text(
        "SELECT page_name, parent_company_id, ig_followers FROM public.advertisers "
        "WHERE id=:a"), {"a": advertiser_id}).first()

    app = conn.execute(text("""
        SELECT ap.id, ap.name, ap.genre, ap.rating_count, ap.release_date, ap.is_emerging
        FROM public.ads a JOIN public.apps ap ON ap.id = a.app_id
        WHERE a.advertiser_id=:a AND a.app_id IS NOT NULL
        ORDER BY a.last_seen DESC NULLS LAST LIMIT 1"""), {"a": advertiser_id}).first()

    release_age = None
    if app and app.release_date:
        release_age = (as_of - app.release_date).days

    has_funnel = conn.execute(text(
        "SELECT count(*) FROM public.funnels f JOIN public.ads a ON a.id=f.ad_id "
        "WHERE a.advertiser_id=:a"), {"a": advertiser_id}).scalar() > 0

    return FeatureVector(
        advertiser_id=advertiser_id,
        cohort=cohort_key(app.genre if app else None, "US"),
        page_name=adv.page_name if adv else None,
        app_id=app.id if app else None,
        app_name=app.name if app else None,
        wow_log_growth=log_growth_wow(series),
        accel=acceleration(series),
        ewma_velocity=ewma(series),
        max_longevity_days=max_long,
        longevity_share=long_share,
        active_ad_count=len(active),
        collation_total=collation_total,
        velocity_series=tuple(series),
        rating_count=app.rating_count if app else None,
        ig_followers=adv.ig_followers if adv else None,
        release_age_days=release_age,
        total_eu_reach=int(reach_latest) if reach_latest is not None else None,
        has_funnel=has_funnel,
        install_cta=install_cta,
        has_app=has_app,
        is_emerging=bool(app.is_emerging) if app else False,
        parent_company_id=adv.parent_company_id if adv else None,
        weeks_observed=len(series),
    )
