"""Embed each scored app's content into frontend.app_embeddings (incremental:
only new or content-changed apps). Mirrors the GemScore pattern — Python writes.

Run:
    PYTHONPATH=src AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf \\
    .venv/bin/python scripts/embed_apps.py
"""
import os
import sys

from sqlalchemy import text

from agf.db.engine import begin, get_engine
from agf.scoring.embeddings import build_embed_text, content_hash, embed_texts

_APPS = text(
    "SELECT m.advertiser_id, m.app_name, m.tagline, m.niche_keyword, "
    "       a.genre, a.description "
    "FROM frontend.emerging_scaling_apps m "
    "LEFT JOIN public.apps a ON a.id = m.app_id")

_AD_COPY = text(
    "SELECT ad.advertiser_id, c.body_text "
    "FROM public.ads ad JOIN public.creatives c ON c.ad_id = ad.id "
    "WHERE ad.advertiser_id = ANY(:ids) AND c.body_text IS NOT NULL "
    "  AND c.body_text <> '' "
    "ORDER BY ad.advertiser_id, ad.meta_rank ASC NULLS LAST, ad.last_seen DESC")

_EXISTING = text("SELECT advertiser_id, content_hash FROM frontend.app_embeddings")

_UPSERT = text(
    "INSERT INTO frontend.app_embeddings "
    "(advertiser_id, embedding, content_hash, dim, model, embedded_at) "
    "VALUES (:a, :e, :h, :d, :m, now()) "
    "ON CONFLICT (advertiser_id) DO UPDATE SET "
    "  embedding=EXCLUDED.embedding, content_hash=EXCLUDED.content_hash, "
    "  dim=EXCLUDED.dim, model=EXCLUDED.model, embedded_at=now()")


def _ad_copy_by_advertiser(conn, ids):
    out: dict[int, list[str]] = {}
    if not ids:
        return out
    for aid, body in conn.execute(_AD_COPY, {"ids": list(ids)}):
        out.setdefault(aid, []).append(body)
    return out


def embed_apps(conn, *, embed_fn=embed_texts, limit=None) -> dict:
    rows = list(conn.execute(_APPS))
    ids = [r.advertiser_id for r in rows]
    ad_copy = _ad_copy_by_advertiser(conn, ids)
    existing = {a: h for a, h in conn.execute(_EXISTING)}

    pending = []  # (advertiser_id, text, hash)
    for r in rows:
        text_ = build_embed_text(
            app_name=r.app_name or "", tagline=r.tagline,
            genre=r.genre, description=r.description,
            ad_copy=ad_copy.get(r.advertiser_id, []))
        if not text_.strip():
            continue
        h = content_hash(text_)
        if existing.get(r.advertiser_id) == h:
            continue
        pending.append((r.advertiser_id, text_, h))
        if limit and len(pending) >= limit:
            break

    embedded = 0
    for aid, text_, h in pending:
        vec = embed_fn([text_])[0]
        conn.execute(_UPSERT, {"a": aid, "e": vec, "h": h, "d": len(vec),
                               "m": "gemini-embedding-001"})
        embedded += 1
    return {"embedded": embedded, "skipped": len(rows) - embedded}


def main() -> int:
    eng = get_engine()
    with begin(eng) as conn:
        stats = embed_apps(conn)
    print(f"[embed] {stats}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
