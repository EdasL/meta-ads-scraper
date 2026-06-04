"""Candidate — the common output of every firehose — and the upsert router (§3.3)."""
from dataclasses import dataclass
from agf.db import repo


@dataclass
class Candidate:
    source: str                       # 'firehose_eu' | 'bridge' | 'firehose_fingerprint'
    kind: str                         # 'page' | 'app' | 'funnel'
    page_id: str | None = None
    page_name: str | None = None
    store: str | None = None          # 'ios' | 'android'
    store_id: str | None = None
    app_meta: dict | None = None      # full app-column dict for upsert_app
    landing_url: str | None = None
    funnel_intent: bool = False
    ad_record: object | None = None   # the originating AdRecord, when from an ad


def upsert_candidate(conn, c: Candidate) -> dict:
    """Route a Candidate into the spine (§3.3). Returns the ids it touched.
    Dedup-safety comes from the repo's ON CONFLICT upserts.

    When the candidate carries its originating `ad_record` (e.g. bridge candidates),
    the ad itself is persisted too — otherwise discovered advertisers would have no
    `ads` rows and Plan 5 could not compute scaling velocity (found via live e2e)."""
    out: dict = {}
    app_id = None
    if c.kind == "app" and (c.store_id or (c.app_meta and c.app_meta.get("store_id"))):
        meta = c.app_meta or {"store": c.store or "ios", "store_id": c.store_id}
        app_id = repo.upsert_app(conn, meta)
        out["app_id"] = app_id
    if c.page_id:                     # page/funnel/app candidates that carry a Meta page
        advertiser_id = repo.upsert_advertiser(conn, c)   # c exposes page_id/page_name
        out["advertiser_id"] = advertiser_id
        if c.ad_record is not None:   # persist the discovered ad (links it to the advertiser)
            out["ad_id"] = repo.upsert_ad(conn, c.ad_record, advertiser_id, app_id)
    return out
