"""Fetch each kept candidate's real store listing by store_id (batch iTunes Lookup +
Play sidecar) and embed name+genre+description with the SAME Spec 2 builder, so the
triage prior can cosine-rank candidates vs the user's liked centroid. Embed-once:
a candidate already in candidate_embeddings is skipped (no re-fetch)."""
from __future__ import annotations

from sqlalchemy import text

from agf.enrich_full.appstore import lookup_google_play, lookup_itunes_batch
from agf.scoring.embeddings import build_embed_text, content_hash, embed_texts

_PENDING = text(
    "SELECT c.id, c.store, c.store_id FROM discovery.candidates c "
    "WHERE c.status='kept' AND c.store_id IS NOT NULL AND NOT EXISTS ("
    "  SELECT 1 FROM discovery.candidate_embeddings e WHERE e.candidate_id = c.id)")

_UPSERT = text(
    "INSERT INTO discovery.candidate_embeddings "
    "(candidate_id, embedding, content_hash, dim, model, embedded_at) "
    "VALUES (:c, :e, :h, :d, :m, now()) "
    "ON CONFLICT (candidate_id) DO UPDATE SET "
    "  embedding=EXCLUDED.embedding, content_hash=EXCLUDED.content_hash, "
    "  dim=EXCLUDED.dim, model=EXCLUDED.model, embedded_at=now()")


def embed_candidates(conn, *, itunes_client, gplay_runner=None,
                     lookup_itunes=lookup_itunes_batch, lookup_play=lookup_google_play,
                     embed_fn=embed_texts, commit_every=25, limit=None) -> dict:
    rows = conn.execute(_PENDING).all()
    if limit is not None:
        rows = rows[:limit]
    ios = [r.store_id for r in rows if r.store == "ios"]
    android = [r.store_id for r in rows if r.store == "android"]
    meta: dict = {}
    if ios:
        meta.update(lookup_itunes(itunes_client, ios))          # {track_id(str): AppMeta}
    if android:
        meta.update(lookup_play(android, runner=gplay_runner))  # {package_id(str): AppMeta}

    embedded = 0
    sentinel = 0
    for r in rows:
        m = meta.get(str(r.store_id))
        if m is None:                                            # delisted / not found
            conn.execute(_UPSERT, {"c": r.id, "e": [], "h": "unfetchable",
                                   "d": 0, "m": "unfetchable"})
            sentinel += 1
            continue
        t = build_embed_text(app_name=m.name or "", tagline=None,
                             genre=m.genre, description=m.description, ad_copy=[])
        if not t.strip():
            conn.execute(_UPSERT, {"c": r.id, "e": [], "h": "unfetchable",
                                   "d": 0, "m": "unfetchable"})
            sentinel += 1
            continue
        vec = embed_fn([t])[0]
        conn.execute(_UPSERT, {"c": r.id, "e": vec, "h": content_hash(t),
                               "d": len(vec), "m": "gemini-embedding-001"})
        embedded += 1
        if commit_every and embedded % commit_every == 0:
            conn.commit()
    return {"embedded": embedded, "sentinel": sentinel, "fetched": len(meta),
            "pending": len(rows)}
