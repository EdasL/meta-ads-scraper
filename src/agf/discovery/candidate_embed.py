"""Embed each kept candidate's store listing (name+genre+description, the SAME Spec 2
builder) so the triage prior can cosine-rank candidates vs the user's liked centroid.

Listing source order: public.apps FIRST (already resolved every sweep by
resolve_pending_app_metadata, chunked lookups), store lookups only for the residue.
Embed-once: a candidate already in candidate_embeddings is skipped (no re-fetch).
Sentinel policy: an id missing from an otherwise-successful lookup is delisted →
permanent 'unfetchable' sentinel; a lookup that returns NOTHING for a non-empty
request is a transient failure → those rows are skipped and retried next run (the
2026-06-29..30 incident permanently poisoned 910 fetchable Android candidates)."""
from __future__ import annotations

from sqlalchemy import bindparam, text

from agf.enrich_full.appstore import lookup_google_play, lookup_itunes_batch
from agf.scoring.embeddings import build_embed_text, content_hash, embed_texts

_PENDING = text(
    "SELECT c.id, c.store, c.store_id FROM discovery.candidates c "
    "WHERE c.status='kept' AND c.store_id IS NOT NULL AND NOT EXISTS ("
    "  SELECT 1 FROM discovery.candidate_embeddings e WHERE e.candidate_id = c.id)")

_APPS_META = text(
    "SELECT store_id, name, genre, description FROM public.apps "
    "WHERE store = :s AND store_id IN :ids AND name IS NOT NULL AND name <> ''"
).bindparams(bindparam("ids", expanding=True))

_UPSERT = text(
    "INSERT INTO discovery.candidate_embeddings "
    "(candidate_id, embedding, content_hash, dim, model, embedded_at) "
    "VALUES (:c, :e, :h, :d, :m, now()) "
    "ON CONFLICT (candidate_id) DO UPDATE SET "
    "  embedding=EXCLUDED.embedding, content_hash=EXCLUDED.content_hash, "
    "  dim=EXCLUDED.dim, model=EXCLUDED.model, embedded_at=now()")


def _write_sentinel(conn, candidate_id):
    conn.execute(_UPSERT, {"c": candidate_id, "e": [], "h": "unfetchable",
                           "d": 0, "m": "unfetchable"})


def embed_candidates(conn, *, itunes_client, gplay_runner=None,
                     lookup_itunes=lookup_itunes_batch, lookup_play=lookup_google_play,
                     embed_fn=embed_texts, commit_every=25, limit=None) -> dict:
    rows = conn.execute(_PENDING).all()
    if limit is not None:
        rows = rows[:limit]

    # Listing source #1: public.apps (already resolved). Store lookups only cover the
    # residue, so a transient bulk-lookup failure can't touch resolved candidates.
    apps_meta: dict = {}
    for store in ("ios", "android"):
        ids = [str(r.store_id) for r in rows if r.store == store]
        if ids:
            for a in conn.execute(_APPS_META, {"s": store, "ids": ids}):
                apps_meta[(store, str(a.store_id))] = a

    ios = [r.store_id for r in rows
           if r.store == "ios" and ("ios", str(r.store_id)) not in apps_meta]
    android = [r.store_id for r in rows
               if r.store == "android" and ("android", str(r.store_id)) not in apps_meta]
    fetched_ios = lookup_itunes(itunes_client, ios) if ios else {}
    fetched_play = lookup_play(android, runner=gplay_runner) if android else {}
    # Nothing back for a non-empty request = transient failure (timeout / API down),
    # NOT proof of delisting — skip those rows this run instead of sentinel-poisoning.
    ios_lookup_ok = not ios or bool(fetched_ios)
    play_lookup_ok = not android or bool(fetched_play)

    embedded = 0
    sentinel = 0
    skipped = 0
    for r in rows:
        a = apps_meta.get((r.store, str(r.store_id)))
        if a is not None:
            t = build_embed_text(app_name=a.name or "", tagline=None,
                                 genre=a.genre, description=a.description, ad_copy=[])
        else:
            fetched = fetched_ios if r.store == "ios" else fetched_play
            m = fetched.get(str(r.store_id))
            if m is None:
                lookup_ok = ios_lookup_ok if r.store == "ios" else play_lookup_ok
                if not lookup_ok:
                    skipped += 1                     # retry next run
                    continue
                _write_sentinel(conn, r.id)          # delisted / not found
                sentinel += 1
                continue
            t = build_embed_text(app_name=m.name or "", tagline=None,
                                 genre=m.genre, description=m.description, ad_copy=[])
        if not t.strip():
            _write_sentinel(conn, r.id)
            sentinel += 1
            continue
        vec = embed_fn([t])[0]
        conn.execute(_UPSERT, {"c": r.id, "e": vec, "h": content_hash(t),
                               "d": len(vec), "m": "gemini-embedding-001"})
        embedded += 1
        if commit_every and embedded % commit_every == 0:
            conn.commit()
    return {"embedded": embedded, "sentinel": sentinel, "skipped": skipped,
            "fetched": len(fetched_ios) + len(fetched_play), "pending": len(rows)}
