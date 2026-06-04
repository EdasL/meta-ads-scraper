"""Creative byte download + dedup → S3 (master-plan §6 P6-T8). Reuses Plan 3 dedup
(sha256 exact + perceptual phash near-dup) and storage.s3. fbcdn signed URLs expire,
so creatives are downloaded at scrape time (the worker re-fetches fresh URLs)."""
from sqlalchemy import text
from agf.db import repo
from agf.db.dedup import sha256_bytes, phash_image, is_duplicate_creative
from agf.storage.s3 import put_bytes
from agf.enrich_full.schemas import CreativeAsset

PHASH_HAMMING_THRESHOLD = 10
_EXT = {"image": "jpg", "video": "mp4"}


def _hamming_hex(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def is_duplicate(conn, sha256, phash, threshold: int = PHASH_HAMMING_THRESHOLD):
    """Existing creative id if this asset duplicates one already stored: exact
    byte-identity (sha256) OR perceptual near-dup (phash hamming < threshold). Else None."""
    exact = is_duplicate_creative(conn, sha256)
    if exact is not None:
        return exact
    if not phash:
        return None
    rows = conn.execute(text(
        "SELECT id, phash FROM public.creatives WHERE phash IS NOT NULL")).all()
    for cid, existing in rows:
        try:
            if _hamming_hex(phash, existing) < threshold:
                return cid
        except (ValueError, TypeError):
            continue
    return None


def _default_fetch(url):
    import httpx
    r = httpx.get(url, follow_redirects=True, timeout=30)
    return r.content, r.headers.get("content-type")


def download_creative(conn, ad_id, url, type, *, body_text=None, title=None,
                      fetch=None, put=None, s3=None,
                      threshold: int = PHASH_HAMMING_THRESHOLD) -> CreativeAsset:
    """Download a creative URL, dedup (sha256 + phash), upload new ones to S3, and
    record the row (§3.3 upsert_creative). Duplicates are NOT re-uploaded; dedup_of
    carries the existing creative id."""
    fetch = fetch or _default_fetch
    put = put or put_bytes
    data, content_type = fetch(url)
    sha = sha256_bytes(data)
    ph = phash_image(data)
    dup = is_duplicate(conn, sha, ph, threshold)
    if dup is not None:
        return CreativeAsset(type=type, sha256=sha, phash=ph, s3_key=None,
                             content_type=content_type, file_size=len(data),
                             creative_id=dup, dedup_of=dup)
    key = f"creatives/{sha}.{_EXT.get(type, 'bin')}"
    put(key, data, content_type or "application/octet-stream", client=s3)
    cid = repo.upsert_creative(conn, ad_id, type, body_text, title, url, key, sha, ph)
    conn.execute(text("UPDATE public.creatives SET content_type=:ct, file_size=:fs WHERE id=:i"),
                 {"ct": content_type, "fs": len(data), "i": cid})
    return CreativeAsset(type=type, sha256=sha, phash=ph, s3_key=key,
                         content_type=content_type, file_size=len(data),
                         creative_id=cid, dedup_of=None)
