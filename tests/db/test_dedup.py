import io
from sqlalchemy import text
from agf.parse import AdRecord
from agf.db import repo
from agf.db.dedup import sha256_bytes, phash_image


def _ad():
    return AdRecord("A1", "P1", "GemCo", True, 1714500000, None, 5,
                    "INSTALL_MOBILE_APP", "Install",
                    "https://apps.apple.com/us/app/x/id111", "body")


def test_sha256_stable_and_distinct():
    assert sha256_bytes(b"abc") == sha256_bytes(b"abc")
    assert sha256_bytes(b"abc") != sha256_bytes(b"abd")


def test_phash_none_for_non_image():
    assert phash_image(b"definitely not an image") is None


def test_phash_hashes_a_real_image():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (123, 222, 64)).save(buf, format="PNG")
    assert isinstance(phash_image(buf.getvalue()), str)


def test_upsert_creative_dedups_identical_bytes(pg_conn):
    adv = repo.upsert_advertiser(pg_conn, _ad())
    ad = repo.upsert_ad(pg_conn, _ad(), adv, None)
    sha = sha256_bytes(b"the-same-image-bytes")
    c1 = repo.upsert_creative(pg_conn, ad, "image", "b", "t", "http://x", "s3k", sha, None)
    c2 = repo.upsert_creative(pg_conn, ad, "image", "b", "t", "http://x", "s3k", sha, None)
    assert c1 == c2
    cnt = pg_conn.execute(text("SELECT count(*) FROM public.creatives WHERE sha256=:s"),
                          {"s": sha}).scalar()
    assert cnt == 1
