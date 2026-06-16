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


def window_log_growth(series) -> float:
    """Overall ramp: log growth from the first NON-ZERO week to the latest (smoothed).

    Unlike `log_growth_wow` (which only sees the most recent week, so a real ramp that has
    plateaued reads as flat), this credits SUSTAINED multi-week scaling — a 30->80 or 0->40
    advertiser scores high even if the last week was flat. Anchoring on the first non-zero
    week makes a 'from zero' ramp the strongest emerging signal instead of a divide-by-zero.
    Flat / <2 points -> 0.0."""
    s = list(series)
    if len(s) < 2:
        return 0.0
    nonzero = [x for x in s if x > 0]
    base = nonzero[0] if nonzero else 0
    return math.log((s[-1] + _EPS) / (base + _EPS))


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


def traction_delta(points, min_span_days: int = 7):
    """(raw_delta, per_day_rate) between the earliest and latest non-None values
    of an ordered [(date, value)] series, or None when <2 usable points or the
    span is under min_span_days (too narrow to call a trend). Per-day
    normalization keeps sparse snapshot histories from deflating the rate."""
    vals = [(d, v) for d, v in points if v is not None]
    if len(vals) < 2:
        return None
    (d0, v0), (d1, v1) = vals[0], vals[-1]
    span = (d1 - d0).days
    if span < min_span_days:
        return None
    return (v1 - v0, (v1 - v0) / span)


_MAX_LAUNCH_AGE_S = 5 * 365 * 86400  # ignore garbage start_dates older than ~5y


def weekly_launch_counts(start_epochs, now_epoch: int):
    """Ad launches per week (zero-filled) from the earliest sane launch up to
    now_epoch, oldest -> newest. The zero weeks matter: the EWMA must see the
    gaps between launches, not just the launches. Empty -> []."""
    week = 7 * 86400
    # drop garbage past dates AND future ones (Meta can schedule ads ahead;
    # a not-yet-started ad is not a launch)
    eps = [e for e in start_epochs
           if e and 0 <= now_epoch - e <= _MAX_LAUNCH_AGE_S]
    if not eps:
        return []
    first = min(eps)
    counts = [0] * (int((now_epoch - first) // week) + 1)
    for e in eps:
        counts[int((e - first) // week)] += 1
    return counts


from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from sqlalchemy import text
from agf.scale import longevity_days
from agf.scoring.cohorts import cohort_key

_MONETIZE_CTAS = {"INSTALL_MOBILE_APP", "SHOP_NOW", "SUBSCRIBE", "SIGN_UP",
                  "GET_OFFER", "ORDER_NOW", "DOWNLOAD", "GET_QUOTE"}
LONGEVITY_FLOOR_DAYS = 14
PROVEN_WINNER_DAYS = 21  # a still-live ad running >= this many days is a proven "winner"


@dataclass(frozen=True)
class FeatureVector:
    advertiser_id: int
    cohort: str
    page_name: str | None
    app_id: int | None
    app_name: str | None
    # scaling-velocity inputs
    wow_log_growth: float
    window_growth: float
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
    # quality-veto inputs (real-gems feed 2026-06-10) — appended with defaults so
    # existing positional constructors keep working; NULL/unknown never vetoes
    is_game: bool | None = None
    installs: int | None = None
    page_like_count: int | None = None
    meta_active_ads: int | None = None
    meta_active_ads_capped: bool | None = None
    # traction maxima across ALL linked store listings (the latest-app fields above
    # miss e.g. a 10M-install Android listing when the newest ad links the iOS app)
    max_installs: int | None = None
    max_rating_count: int | None = None
    # GemScore v2 (spec 2026-06-11): demand-velocity + iteration inputs.
    # Velocities are per-day rates over the trailing 28d of adtech.app_metrics;
    # the raw deltas are persisted for the UI (real numbers, never re-derived).
    installs_velocity: float | None = None
    review_velocity: float | None = None
    installs_delta_28d: int | None = None
    rating_count_delta_28d: int | None = None
    iteration_rate: float = 0.0          # EWMA of weekly ad-launch counts
    proven_winner_count: int = 0         # live ads running >= PROVEN_WINNER_DAYS (winner_depth)
    count_growth: float = 0.0            # max(0, window_growth): rising ad count only, never negative


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
    proven_winners = sum(1 for d in longs if d >= PROVEN_WINNER_DAYS)
    collation_total = sum(int(r.collation_count or 0) for r in active)
    install_cta = any((r.cta_type or "") in _MONETIZE_CTAS for r in active)
    has_app = any(r.app_id is not None for r in ad_rows)

    adv = conn.execute(text(
        "SELECT page_name, parent_company_id, ig_followers, "
        "       page_like_count, meta_active_ads, meta_active_ads_capped "
        "FROM public.advertisers WHERE id=:a"), {"a": advertiser_id}).first()

    app = conn.execute(text("""
        SELECT ap.id, ap.name, ap.genre, ap.rating_count, ap.release_date, ap.is_emerging,
               ap.is_game, ap.installs
        FROM public.ads a JOIN public.apps ap ON ap.id = a.app_id
        WHERE a.advertiser_id=:a AND a.app_id IS NOT NULL
        ORDER BY a.last_seen DESC NULLS LAST LIMIT 1"""), {"a": advertiser_id}).first()

    # traction maxima across EVERY linked listing (iOS + Android), for the
    # established_traction veto — the latest-app row alone can hide a huge sibling
    traction = conn.execute(text("""
        SELECT max(ap.installs) AS mi, max(ap.rating_count) AS mr
        FROM public.ads a JOIN public.apps ap ON ap.id = a.app_id
        WHERE a.advertiser_id=:a AND a.app_id IS NOT NULL"""), {"a": advertiser_id}).first()

    release_age = None
    if app and app.release_date:
        release_age = (as_of - app.release_date).days

    # demand velocity: store-traction deltas over the trailing 28 days
    installs_vel = review_vel = installs_delta = reviews_delta = None
    if app:
        snaps = conn.execute(text(
            "SELECT snapshot_date, rating_count, installs FROM adtech.app_metrics "
            "WHERE app_id=:i AND snapshot_date >= :w0 AND snapshot_date <= :d "
            "ORDER BY snapshot_date"),
            {"i": app.id, "w0": as_of - timedelta(days=28), "d": as_of}).all()
        inst = traction_delta([(r.snapshot_date, r.installs) for r in snaps])
        revs = traction_delta([(r.snapshot_date, r.rating_count) for r in snaps])
        if inst is not None:
            installs_delta, installs_vel = inst
        if revs is not None:
            reviews_delta, review_vel = revs

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
        window_growth=window_log_growth(series),
        accel=acceleration(series),
        # smooth the weekly *velocity* (deltas), not the level — else a big flat
        # incumbent (high count, zero growth) outscores a small fast-growing gem.
        ewma_velocity=ewma([series[i] - series[i - 1] for i in range(1, len(series))]),
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
        is_game=app.is_game if app else None,
        installs=app.installs if app else None,
        page_like_count=adv.page_like_count if adv else None,
        meta_active_ads=adv.meta_active_ads if adv else None,
        meta_active_ads_capped=adv.meta_active_ads_capped if adv else None,
        max_installs=int(traction.mi) if traction and traction.mi is not None else None,
        max_rating_count=int(traction.mr) if traction and traction.mr is not None else None,
        installs_velocity=installs_vel,
        review_velocity=review_vel,
        installs_delta_28d=int(installs_delta) if installs_delta is not None else None,
        rating_count_delta_28d=int(reviews_delta) if reviews_delta is not None else None,
        # every stored ad counts as a launch (ended ones too): iteration is
        # "how often do they ship new creatives", not "what is still running"
        iteration_rate=ewma(weekly_launch_counts(
            [r.start_epoch for r in ad_rows if r.start_epoch], now_epoch)),
        proven_winner_count=proven_winners,
        # rising ad count is a small bonus; a FALLING count clamps to 0 so winner
        # consolidation (20 ads -> 2 winners) is never punished. window_growth is the
        # sustained multi-week ramp already computed above.
        count_growth=max(0.0, window_log_growth(series)),
    )
