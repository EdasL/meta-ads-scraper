"""Stage 2 of breadth-first discovery (spec 2026-06-10): the free keep/drop gate.

Decision inputs come ONLY from store lookups (iTunes batch / Play sidecar — both
direct, zero proxy) plus what the sweep already stored. Thresholds are imported
from scoring.vetoes (the user-set constants) — never redefined here. release_date
is recorded on the app row but is NOT a gate (an older app that just started
scaling ads must still get in).

Failure semantics: a lookup that raises (or a Play sidecar crash, which surfaces
as an empty dict while ids were pending) leaves its candidates at status 'new' so
tomorrow's run retries them; an id absent from a SUCCESSFUL lookup is a dead/
missing US listing -> dropped. The empty-dict-means-crash rule applies only to
the Play sidecar — an empty iTunes batch response is a real "all delisted"."""
from types import SimpleNamespace

from agf.db import repo
from agf.db.candidates import mark_triaged, new_candidates
from agf.scoring.vetoes import ESTABLISHED_RATINGS_MAX


def triage_decision(meta) -> tuple[str, str | None]:
    """('kept'|'dropped', drop_reason). Pure; NULL/unknown fields always pass.

    Gated on rating_count only — NOT installs. Play's cumulative maxInstalls was
    dropping ~83% of Android candidates on a signal iOS apps never expose, skewing the
    feed ~90/10 to iOS (user 2026-06-27). Both stores now use the comparable axis."""
    if meta is None:
        return "dropped", "no_us_listing"
    if meta.is_game is True:
        return "dropped", "game"
    if (meta.rating_count or 0) > ESTABLISHED_RATINGS_MAX:
        return "dropped", "ratings_over_threshold"
    return "kept", None


def _app_dict(store: str, store_id: str, meta) -> dict:
    """AppMeta -> upsert_app dict with an EXPLICIT store. (repo._app_dict assumes a
    bare AppMeta is iOS, which would mislabel Android keepers.)"""
    return {"store": store, "store_id": store_id, "name": meta.name,
            "developer": meta.seller, "genre": meta.genre,
            "release_date": meta.release_date, "rating": meta.avg_rating,
            "rating_count": meta.rating_count, "installs": meta.installs,
            "icon_url": meta.icon_url, "screenshots": meta.screenshots,
            "price_label": meta.price_label, "description": meta.description,
            "is_game": meta.is_game}


def _promote(conn, row, meta) -> int:
    advertiser_id = repo.upsert_advertiser(conn, SimpleNamespace(
        page_id=row.page_id, page_name=row.page_name,
        page_like_count=row.page_like_count))
    repo.upsert_app(conn, _app_dict(row.store, row.store_id, meta))
    return advertiser_id


def run_triage(conn, *, lookup_ios, lookup_android, limit=None, only_ids=None) -> dict:
    """Triage status='new' candidates (oldest first). lookup_*: callable(ids)->{store_id:
    AppMeta}. `limit` caps how many are examined per run so a backlog drains in bounded
    slices instead of one unbounded pass. `only_ids` scopes triage to an id allowlist
    (a hunt round triages just its own candidates — default None = whole backlog, the
    nightly tick). conn-first, no commit — the caller owns the txn."""
    rows = new_candidates(conn, limit=limit, only_ids=only_ids)
    stats = {"examined": len(rows), "kept": 0, "dropped": {},
             "lookup_failed_ios": False, "lookup_failed_android": False}
    ios_rows = [r for r in rows if r.store == "ios"]
    android_rows = [r for r in rows if r.store == "android"]

    def _resolve(group, lookup, failed_key, empty_is_failure):
        ids = [r.store_id for r in group]
        if not ids:
            return {}
        try:
            found = lookup(ids)
        except Exception:                       # network/API failure -> retry tomorrow
            stats[failed_key] = True
            return None
        if not found and empty_is_failure:      # sidecar crash / empty API response while
            stats[failed_key] = True            # ids were pending: can't tell dead from down
            return None
        return found

    for group, lookup, failed_key, empty_is_failure in (
            (ios_rows, lookup_ios, "lookup_failed_ios", False),
            (android_rows, lookup_android, "lookup_failed_android", True)):
        found = _resolve(group, lookup, failed_key, empty_is_failure)
        if found is None:
            continue                            # whole group stays 'new'
        for row in group:
            meta = found.get(str(row.store_id))
            status, reason = triage_decision(meta)
            if status == "kept":
                advertiser_id = _promote(conn, row, meta)
                mark_triaged(conn, row.id, status="kept",
                             kept_advertiser_id=advertiser_id)
                stats["kept"] += 1
            else:
                mark_triaged(conn, row.id, status="dropped", drop_reason=reason)
                stats["dropped"][reason] = stats["dropped"].get(reason, 0) + 1
    return stats
