"""Keyword redundancy gate. A candidate keyword semantically close to a basin we've
already mined out is likely to re-find the known corpus, so we drop it before it costs
a sweep. Novelty = 1 - max cosine similarity to any exhausted-basin keyword embedding.
Embeddings are L2-normalized (cosine = dot product). Taste-blind.

See docs/superpowers/specs/2026-07-06-keyword-novelty-engine-design.md."""
from agf.scoring.embeddings import embed_texts


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


def admit_by_novelty(scored: dict[str, float], *, threshold: float, limit: int) -> list[str]:
    """Drop phrases scoring >= threshold (too close to an exhausted basin); return the
    rest sorted ascending by score (most novel first), capped at limit."""
    kept = [(p, s) for p, s in scored.items() if s < threshold]
    kept.sort(key=lambda ps: (ps[1], ps[0]))
    return [p for p, _ in kept[:max(0, limit)]]
