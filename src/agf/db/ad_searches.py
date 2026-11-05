"""Ad-search snapshots. All writes stay separate from discovery and enrichment."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import text

from agf.parse import AdRecord


def load_search(conn, search_id: int):
    return (
        conn.execute(
            text("SELECT * FROM frontend.ad_searches WHERE id=:id"), {"id": search_id}
        )
        .mappings()
        .first()
    )


def update_search(conn, search_id: int, *, status=None, progress=None, error=None):
    values = {"id": search_id}
    fields = []
    if status is not None:
        fields += ["status=:status", "error_text=:error"]
        values.update(status=status, error=error)
        if status == "running":
            fields.append("started_at=COALESCE(started_at, now())")
        elif status in ("done", "error"):
            fields.append("finished_at=now()")
    for name, value in [("progress", progress)]:
        if value is not None:
            fields.append(f"{name}=CAST(:{name} AS jsonb)")
            values[name] = json.dumps(value, allow_nan=False)
    if fields:
        conn.execute(
            text(
                "UPDATE frontend.ad_searches SET " + ", ".join(fields) + " WHERE id=:id"
            ),
            values,
        )


def creative_hash(ad: AdRecord) -> str:
    def media_key(url):
        p = urlsplit(url)
        return urlunsplit(("", "", p.path, "", ""))

    body = " ".join((ad.body_text or "").casefold().split())
    media = sorted(media_key(u) for u in ad.image_urls + ad.video_urls)
    parts = [body, *media]
    if not body and not media:
        parts.append(str(ad.ad_archive_id))
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def save_ads(
    conn,
    search_id: int,
    ads: list[AdRecord],
    *,
    source_term: str,
    country: str,
    observed_at=None,
) -> list[str]:
    saved = []
    for ad in ads:
        if not ad.ad_archive_id or not ad.page_id or not ad.is_active:
            continue
        row = asdict(ad)
        for key in ("start_date", "end_date"):
            value = row[key]
            try:
                row[key] = datetime.fromtimestamp(value, UTC) if value else None
            except (ValueError, OverflowError, OSError):
                row[key] = None
        row.update(
            search_id=search_id,
            source_term=source_term,
            country=country,
            creative_hash=creative_hash(ad),
            observed_at=observed_at or datetime.now(UTC),
        )
        for key in ("image_urls", "video_urls"):
            row[key] = json.dumps(row[key])
        conn.execute(
            text("""
            INSERT INTO frontend.ad_search_results
              (search_id,ad_archive_id,page_id,page_name,source_term,country,
               is_active,start_date,end_date,collation_count,page_like_count,
               cta_type,cta_text,link_url,body_text,image_urls,video_urls,
               creative_hash,observed_at)
            VALUES (:search_id,:ad_archive_id,:page_id,:page_name,:source_term,:country,
               :is_active,:start_date,:end_date,:collation_count,:page_like_count,
               :cta_type,:cta_text,:link_url,:body_text,CAST(:image_urls AS jsonb),
               CAST(:video_urls AS jsonb),:creative_hash,:observed_at)
            ON CONFLICT (search_id,ad_archive_id) DO NOTHING
        """),
            row,
        )
        saved.append(ad.ad_archive_id)
    return saved
