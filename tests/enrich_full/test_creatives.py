import io
from sqlalchemy import text
from agf.parse import AdRecord
from agf.db import repo
from agf.enrich_full.creatives import is_duplicate, download_creative, _hamming_hex


def _png(color):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color).save(buf, format="PNG")
    return buf.getvalue()


def _ad(ext="C1"):
    return AdRecord(ext, "PC", "GemCo", True, 1_714_500_000, None, 1,
                    "INSTALL_MOBILE_APP", "Install", "https://x/y", "body")


def test_hamming_hex_counts_bit_differences():
    assert _hamming_hex("ffff", "ffff") == 0
    assert _hamming_hex("ffff", "fffe") == 1
    assert _hamming_hex("0000", "ffff") == 16


def test_is_duplicate_exact_then_near(pg_conn):
    adv = repo.upsert_advertiser(pg_conn, _ad())
    ad = repo.upsert_ad(pg_conn, _ad(), adv, None)
    repo.upsert_creative(pg_conn, ad, "image", "b", "t", None, "k", "sha-A", "ffffffffffffffff")
    assert is_duplicate(pg_conn, "sha-A", None) == \
        pg_conn.execute(text("SELECT id FROM public.creatives WHERE sha256='sha-A'")).scalar()
    # near phash (1 bit) -> dup; far phash -> None
    near = is_duplicate(pg_conn, "sha-NEW", "fffffffffffffffe", threshold=10)
    assert near is not None
    assert is_duplicate(pg_conn, "sha-NEW2", "0000000000000000", threshold=10) is None


def test_download_creative_uploads_then_dedups_identical(pg_conn):
    adv = repo.upsert_advertiser(pg_conn, _ad("C2"))
    ad = repo.upsert_ad(pg_conn, _ad("C2"), adv, None)
    data = _png((10, 120, 200))
    puts = []
    fake_put = lambda key, d, ct, client=None: (puts.append(key) or key)
    fetch = lambda url: (data, "image/png")

    a1 = download_creative(pg_conn, ad, "https://cdn/x.png", "image", fetch=fetch, put=fake_put)
    a2 = download_creative(pg_conn, ad, "https://cdn/x.png", "image", fetch=fetch, put=fake_put)

    assert a1.dedup_of is None and a1.s3_key and a1.creative_id
    assert a2.dedup_of == a1.creative_id          # identical bytes -> deduped
    assert len(puts) == 1                          # dup NOT re-uploaded
    cnt = pg_conn.execute(text("SELECT count(*) FROM public.creatives WHERE sha256=:s"),
                          {"s": a1.sha256}).scalar()
    assert cnt == 1
    row = pg_conn.execute(text("SELECT content_type, file_size FROM public.creatives WHERE id=:i"),
                          {"i": a1.creative_id}).first()
    assert row.content_type == "image/png" and row.file_size == len(data)
