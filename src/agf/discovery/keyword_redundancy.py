"""Keyword redundancy gate. A candidate keyword semantically close to a basin we've
already mined out is likely to re-find the known corpus, so we drop it before it costs
a sweep. Novelty = 1 - max cosine similarity to any exhausted-basin keyword embedding.
Embeddings are L2-normalized (cosine = dot product). Taste-blind.

See docs/superpowers/specs/2026-07-06-keyword-novelty-engine-design.md."""
from sqlalchemy import text

from agf.scoring.embeddings import content_hash, embed_texts


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def redundancy_scores(phrases, basin_vectors, *, embed=embed_texts) -> dict[str, float]:
    """Max cosine similarity of each phrase to any exhausted-basin vector. No basins →
    every phrase scores 0.0 (cold-start admits freely). Embeds all phrases in one call."""
    phrases = list(phrases)
    if not phrases:
        return {}
    vecs = embed(phrases)
    if not basin_vectors:
        return {p: 0.0 for p in phrases}
    return {p: max(_dot(v, b) for b in basin_vectors)
            for p, v in zip(phrases, vecs)}


def _token_set(keyword) -> frozenset:
    """Normalized token-set of a keyword: lowercase, whitespace-split, and drop a single
    trailing 'app' (the near-universal head-term suffix — stripped on both sides so a
    candidate that only adds 'app' is NOT counted as a real extra modifier)."""
    toks = str(keyword).lower().split()
    if len(toks) > 1 and toks[-1] == "app":
        toks = toks[:-1]
    return frozenset(toks)


def drop_token_supersets(candidates, existing) -> list[str]:
    """Drop any candidate whose normalized token-set is a STRICT superset of some existing
    keyword's token-set — i.e. the candidate is an existing head-phrase plus extra modifier
    words ('daily gratitude journal app' vs the existing 'gratitude journal app'), an
    over-specified re-find of a niche we already cover. Keeps candidates that are subsets
    (broader), disjoint, or an exact set match (exact dups are handled elsewhere). Cheap,
    pure-Python (no embeddings); order-stable. Complements the embedding-basin gate."""
    existing_sets = [s for s in (_token_set(e) for e in existing) if s]
    out = []
    for c in candidates:
        cs = _token_set(c)
        if cs and any(cs > es for es in existing_sets):
            continue
        out.append(c)
    return out


def admit_by_novelty(scored: dict[str, float], *, threshold: float, limit: int) -> list[str]:
    """Drop phrases scoring >= threshold (too close to an exhausted basin); return the
    rest sorted ascending by score (most novel first), capped at limit."""
    kept = [(p, s) for p, s in scored.items() if s < threshold]
    kept.sort(key=lambda ps: (ps[1], ps[0]))
    return [p for p, _ in kept[:max(0, limit)]]


def _basin_rows(conn, *, dry_floor):
    """(id, keyword, cached embedding|None) for every exhausted-basin keyword:
    status='retired', or live (trial/active) with dry_streak >= dry_floor."""
    return conn.execute(text(
        "SELECT k.id, k.keyword, e.embedding "
        "FROM discovery.dynamic_keywords k "
        "LEFT JOIN discovery.dynamic_keyword_embeddings e ON e.keyword_id = k.id "
        "WHERE k.status='retired' OR (k.status IN ('trial','active') AND k.dry_streak >= :f) "
        "ORDER BY k.id"),
        {"f": dry_floor}).all()


def _cache_embedding(conn, keyword_id, keyword, vec):
    conn.execute(text(
        "INSERT INTO discovery.dynamic_keyword_embeddings "
        "(keyword_id, embedding, content_hash, dim, model) "
        "VALUES (:i, :e, :h, :d, :m) ON CONFLICT (keyword_id) DO NOTHING"),
        {"i": keyword_id, "e": vec, "h": content_hash(keyword), "d": len(vec),
         "m": "gemini-embedding-001"})


def _split_cached(rows):
    vectors, missing = [], []
    for r in rows:
        if r.embedding is not None:
            vectors.append([float(x) for x in r.embedding])
        else:
            missing.append((r.id, r.keyword))
    return vectors, missing


def exhausted_basin_embeddings(conn, *, dry_floor, embed=embed_texts) -> list[list[float]]:
    """Basin embeddings on an already-open conn: reads cached vectors from
    dynamic_keyword_embeddings; lazily embeds + caches any basin keyword not yet cached.
    NOTE: the lazy embed is network I/O on the caller's conn/txn — hot paths should use
    load_basin_embeddings(engine) instead, which keeps the embed outside any transaction.
    Returns just the vectors (order irrelevant — the gate takes a max over them)."""
    vectors, missing = _split_cached(_basin_rows(conn, dry_floor=dry_floor))
    if missing:
        new_vecs = embed([kw for _, kw in missing])
        for (kid, kw), vec in zip(missing, new_vecs):
            _cache_embedding(conn, kid, kw, vec)
            vectors.append(vec)
    return vectors


def load_basin_embeddings(engine, *, dry_floor, embed=embed_texts,
                          lazy_limit=200) -> list[list[float]]:
    """Txn-safe basin load for hot paths: read cached vectors in a short read, embed at
    most `lazy_limit` uncached basin keywords OUTSIDE any transaction (never the full
    basin set — a cold cache catches up over runs or via the out-of-band pre-warm), then
    cache the new vectors in a short write txn. Returns the vectors."""
    with engine.connect() as conn:
        vectors, missing = _split_cached(_basin_rows(conn, dry_floor=dry_floor))
    missing = missing[:max(0, lazy_limit)]
    if missing:
        new_vecs = embed([kw for _, kw in missing])  # network — no txn held
        with engine.begin() as conn:
            for (kid, kw), vec in zip(missing, new_vecs):
                _cache_embedding(conn, kid, kw, vec)
                vectors.append(vec)
    return vectors
