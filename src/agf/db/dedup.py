import hashlib
from io import BytesIO
from sqlalchemy import text


def sha256_bytes(data: bytes) -> str:
    """Hex SHA-256 of raw bytes (exact-dup key)."""
    return hashlib.sha256(data).hexdigest()


def phash_image(data: bytes) -> str | None:
    """Perceptual hash of image bytes as a hex string; None for non-image bytes."""
    try:
        from PIL import Image
        import imagehash
        with Image.open(BytesIO(data)) as im:
            return str(imagehash.phash(im))
    except Exception:
        return None


def is_duplicate_creative(conn, sha256: str | None, phash: str | None = None):
    """Return the id of an existing creative with the same sha256 (exact dup), else None.
    (Near-dup phash matching is Plan 6; here we gate on byte-identity.)"""
    if not sha256:
        return None
    row = conn.execute(
        text("SELECT id FROM public.creatives WHERE sha256 = :s LIMIT 1"), {"s": sha256}
    ).first()
    return row[0] if row else None
