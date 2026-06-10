"""Recent store reviews per app (gem-signals spec, Layer 1 item 2).

iOS: the customer-reviews RSS works ONLY in the lowercase paged form
`https://itunes.apple.com/{cc}/rss/customerreviews/page=N/id=<id>/sortby=mostrecent/json`
— camelCase `sortBy` on the paged URL silently returns an empty feed (live-probed
2026-06-10). 50 reviews/page, 10-page cap; we take page 1 and trim to ~20.

Android: `gplay.reviews()` NEWEST via the gp_reviews.js Node sidecar (same
google-play-scraper install as gp_lookup.js — zero new infrastructure).

Missing/empty feeds are normal for emerging apps — every fetcher degrades to []."""
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_REVIEWS_SIDECAR = Path(__file__).parent / "sidecar" / "gp_reviews.js"
_ITUNES_REVIEWS_URL = ("https://itunes.apple.com/{country}/rss/customerreviews/"
                       "page={page}/id={track_id}/sortby=mostrecent/json")


@dataclass
class ReviewRecord:
    review_id: str
    author: str | None
    rating: int | None
    title: str | None
    content: str | None
    reviewed_at: datetime | None


def _dig(node, *keys):
    """Walk nested dicts; None as soon as a step is missing (RSS JSON is `.label`-heavy)."""
    cur = node
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_rating(value):
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


# --- iOS ----------------------------------------------------------------------
def parse_itunes_reviews(payload) -> list[ReviewRecord]:
    """Parse a customer-reviews RSS JSON feed. Entries without `im:rating` (e.g. an
    app-summary entry) are skipped; a single review arrives as a dict, not a list."""
    entries = _dig(payload or {}, "feed", "entry") or []
    if isinstance(entries, dict):
        entries = [entries]
    out: list[ReviewRecord] = []
    for e in entries:
        review_id = _dig(e, "id", "label")
        rating_raw = _dig(e, "im:rating", "label")
        if not review_id or rating_raw is None:
            continue
        out.append(ReviewRecord(
            review_id=str(review_id),
            author=_dig(e, "author", "name", "label"),
            rating=_parse_rating(rating_raw),
            title=_dig(e, "title", "label"),
            content=_dig(e, "content", "label"),
            reviewed_at=_parse_dt(_dig(e, "updated", "label"))))
    return out


def fetch_itunes_reviews(client, track_id, *, country="us", page=1,
                         limit=20) -> list[ReviewRecord]:
    """Fetch the most-recent iOS reviews for a track id (page 1 = newest 50; trimmed to
    `limit`). A bad id / empty feed / non-JSON body degrades to []."""
    url = _ITUNES_REVIEWS_URL.format(country=country, page=page, track_id=track_id)
    resp = client.get(url)
    try:
        payload = resp.json() or {}
    except (ValueError, TypeError):
        return []
    return parse_itunes_reviews(payload)[:limit]


# --- Android -------------------------------------------------------------------
def _default_reviews_runner(package_id, num):
    return subprocess.run(["node", str(_REVIEWS_SIDECAR), str(package_id), str(num)],
                          capture_output=True, text=True, timeout=90).stdout


def parse_gplay_reviews(rows) -> list[ReviewRecord]:
    out: list[ReviewRecord] = []
    for r in rows or []:
        if not isinstance(r, dict) or r.get("error") or not r.get("id"):
            continue
        out.append(ReviewRecord(
            review_id=str(r["id"]),
            author=r.get("userName"),
            rating=_parse_rating(r.get("score")),
            title=r.get("title"),
            content=r.get("text"),
            reviewed_at=_parse_dt(r.get("date"))))
    return out


def fetch_gplay_reviews(package_id, *, num=20, runner=None) -> list[ReviewRecord]:
    """Fetch the newest Android reviews via the gp_reviews.js sidecar.
    `runner(package_id, num) -> stdout` is injectable for tests; malformed sidecar
    output degrades to []."""
    runner = runner or _default_reviews_runner
    raw = runner(package_id, num) or "[]"
    try:
        rows = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(rows, list):
        return []
    return parse_gplay_reviews(rows)[:num]
