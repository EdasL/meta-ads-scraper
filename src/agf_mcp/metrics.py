"""Curated read-only metrics for the scraping engine.

Two areas:
  * discovery throughput  -> engine_pulse, discovery_rate
  * app-idea diversity     -> idea_diversity (semantic, from store-listing
                              embeddings)
  * scraping_overview combines them into one dashboard.

Schema notes (verified 2026-06-30):
  * discovery.candidates: first_seen_at (not first_seen), store (not platform),
    status in (new, kept, dropped), kept_advertiser_id (bigint).
  * discovery.candidate_embeddings: embedding float4[]; dim=0 / model=
    'unfetchable' rows are placeholders for failed listing fetches -> filter to
    dim=768.
  * frontend.emerging_scaling_products: advertiser_id (integer), gem_score; no
    category column -> idea diversity is semantic, not category-based.
"""

from __future__ import annotations

import math

import numpy as np

from . import db

GEM_GATE = 60
EMBED_DIM = 768


# --------------------------------------------------------------------------- #
# Discovery throughput
# --------------------------------------------------------------------------- #

def engine_pulse(url: str | None = None) -> dict:
    """Is the engine alive or wedged? Newest candidate + recent run activity."""
    newest = db.run_rows(
        "SELECT max(first_seen_at) AS ts FROM discovery.candidates", url=url
    )[0]["ts"]
    backlog = db.run_rows(
        "SELECT count(*) AS n FROM discovery.candidates WHERE status='new'", url=url
    )[0]["n"]

    runs = {}
    for tbl in ("sweep_runs", "flywheel_runs", "funnel_sweep_runs"):
        row = db.run_rows(
            f"""SELECT max(started_at) AS latest,
                       count(*) FILTER (WHERE started_at > now() - interval '24 hours') AS d1,
                       count(*) FILTER (WHERE started_at > now() - interval '7 days')  AS d7
                FROM discovery.{tbl}""",
            url=url,
        )[0]
        runs[tbl] = {"latest": _iso(row["latest"]), "runs_24h": row["d1"],
                     "runs_7d": row["d7"]}

    # Wedged heuristic: no candidate in 6h, or no sweep in 24h.
    age_h = _age_hours(newest)
    stale_candidates = age_h is not None and age_h > 6
    no_recent_sweep = runs["sweep_runs"]["runs_24h"] == 0
    status = "wedged" if (stale_candidates or no_recent_sweep) else "healthy"

    return {
        "status": status,
        "newest_candidate": _iso(newest),
        "newest_candidate_age_hours": _round(_age_hours(newest)),
        "triage_backlog_new": backlog,
        "runs": runs,
        "flags": {
            "stale_candidates_6h": stale_candidates,
            "no_sweep_24h": no_recent_sweep,
        },
    }


def discovery_rate(days: int = 14, url: str | None = None) -> dict:
    """New candidates/day, status mix + kept-rate, source-keyword breadth."""
    days = _clamp_days(days)
    per_day = db.run_rows(
        """SELECT to_char(date_trunc('day', first_seen_at), 'YYYY-MM-DD') AS day,
                  count(*) AS n
           FROM discovery.candidates
           WHERE first_seen_at > now() - make_interval(days => :d)
           GROUP BY 1 ORDER BY 1""",
        {"d": days}, url=url,
    )
    status_mix = {r["status"]: r["n"] for r in db.run_rows(
        "SELECT status, count(*) AS n FROM discovery.candidates GROUP BY 1", url=url
    )}
    kept = status_mix.get("kept", 0)
    dropped = status_mix.get("dropped", 0)
    triaged = kept + dropped
    kept_rate = round(kept / triaged, 3) if triaged else None

    keyword_breadth = db.run_rows(
        """SELECT count(DISTINCT source_keyword) AS n
           FROM discovery.candidates
           WHERE first_seen_at > now() - make_interval(days => :d)""",
        {"d": days}, url=url,
    )[0]["n"]

    counts = [r["n"] for r in per_day]
    return {
        "window_days": days,
        "per_day": per_day,
        "total_in_window": sum(counts),
        "avg_per_day": _round(sum(counts) / len(counts)) if counts else 0,
        "status_mix": status_mix,
        "kept_rate": kept_rate,
        "distinct_source_keywords_in_window": keyword_breadth,
    }


# --------------------------------------------------------------------------- #
# App-idea diversity (semantic)
# --------------------------------------------------------------------------- #

def idea_diversity(k: int | None = None, url: str | None = None) -> dict:
    """Semantic diversity of discovered app ideas, from listing embeddings.

    Operates on the whole kept corpus (diversity is a corpus property): reports
    effective number of concepts, cluster concentration (corpus + gems),
    human-readable concept groups, embedding coverage, and novelty (newest finds
    vs the rest).
    """
    coverage = db.run_rows(
        """SELECT count(*) FILTER (WHERE e.dim = :dim) AS valid,
                  count(*) AS total_kept_with_row
           FROM discovery.candidates c
           LEFT JOIN discovery.candidate_embeddings e ON e.candidate_id = c.id
           WHERE c.status = 'kept'""",
        {"dim": EMBED_DIM}, url=url,
    )[0]
    rows = db.run_rows(
        """SELECT e.candidate_id,
                  e.embedding,
                  c.store,
                  c.page_name,
                  c.source_keyword,
                  c.first_seen_at,
                  (f.advertiser_id IS NOT NULL AND f.gem_score >= :gate) AS is_gem
           FROM discovery.candidate_embeddings e
           JOIN discovery.candidates c ON c.id = e.candidate_id
           LEFT JOIN frontend.emerging_scaling_products f
                  ON f.advertiser_id = c.kept_advertiser_id::int
           WHERE e.dim = :dim AND c.status = 'kept'""",
        {"gate": GEM_GATE, "dim": EMBED_DIM}, url=url,
    )

    cov = {"valid_embeddings": coverage["valid"],
           "kept_candidates": coverage["total_kept_with_row"],
           "coverage_pct": _round(
               coverage["valid"] / coverage["total_kept_with_row"]
               if coverage["total_kept_with_row"] else None)}

    n = len(rows)
    if n < 2:
        return {"n_embeddings": n, "embedding_coverage": cov,
                "note": "not enough valid embeddings to measure idea diversity"}

    vecs = np.asarray([r["embedding"] for r in rows], dtype=np.float32)
    vecs = _l2_normalize(vecs)
    is_gem = np.asarray([bool(r["is_gem"]) for r in rows])
    ages = np.asarray([_age_days(r["first_seen_at"]) for r in rows], dtype=float)

    k = k or min(30, max(2, n // 20))
    labels, centroids = _kmeans(vecs, k)

    sizes = np.bincount(labels, minlength=centroids.shape[0])
    nonempty = sizes[sizes > 0]
    eff = _effective_count(nonempty)

    # Concentration: share of the biggest concepts (corpus + gems).
    corpus_conc = _concentration(sizes)
    gem_sizes = np.bincount(labels[is_gem], minlength=centroids.shape[0]) \
        if is_gem.any() else np.zeros_like(sizes)
    gem_conc = _concentration(gem_sizes)

    groups = _concept_groups(rows, labels, vecs, centroids)

    novelty = _novelty(vecs, ages)

    plat = {"kept": _store_split(rows, None),
            "gems": _store_split(rows, is_gem)}

    return {
        "n_embeddings": n,
        "embedding_coverage": cov,
        "n_gems": int(is_gem.sum()),
        "n_clusters": int((sizes > 0).sum()),
        "effective_concepts": _round(eff),
        "concentration": {"corpus": corpus_conc, "gems": gem_conc},
        "concept_groups": groups,
        "novelty": novelty,
        "platform_split": plat,
    }


def scraping_overview(url: str | None = None) -> dict:
    """One dashboard call: pulse + discovery rate + idea diversity."""
    return {
        "engine_pulse": engine_pulse(url=url),
        "discovery_rate": discovery_rate(url=url),
        "idea_diversity": idea_diversity(url=url),
    }


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _l2_normalize(v: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(v, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return v / norms


def _kmeans(vecs: np.ndarray, k: int):
    """Deterministic k-means on unit vectors (cosine ~ euclidean on the sphere)."""
    from scipy.cluster.vq import kmeans2
    k = min(k, vecs.shape[0])
    centroids, labels = kmeans2(vecs.astype(np.float64), k, minit="++",
                                seed=0, missing="warn")
    return labels.astype(int), centroids


def _effective_count(sizes: np.ndarray) -> float:
    """exp(Shannon entropy) over cluster sizes — 'evenly-used concept count'."""
    p = sizes / sizes.sum()
    p = p[p > 0]
    entropy = -np.sum(p * np.log(p))
    return float(math.exp(entropy))


def _concentration(sizes: np.ndarray) -> dict:
    total = int(sizes.sum())
    if total == 0:
        return {"total": 0, "top1_share": None, "top5_share": None}
    ordered = np.sort(sizes)[::-1]
    return {
        "total": total,
        "top1_share": _round(ordered[0] / total),
        "top5_share": _round(ordered[:5].sum() / total),
    }


def _concept_groups(rows, labels, vecs, centroids, top=12) -> list[dict]:
    """Largest clusters, each labeled by its most-central app + size."""
    sizes = np.bincount(labels, minlength=centroids.shape[0])
    order = np.argsort(sizes)[::-1]
    groups = []
    for cid in order:
        size = int(sizes[cid])
        if size == 0:
            continue
        members = np.where(labels == cid)[0]
        sims = vecs[members] @ centroids[cid]
        rep = members[int(np.argmax(sims))]
        groups.append({
            "size": size,
            "representative_app": rows[rep]["page_name"],
            "representative_keyword": rows[rep]["source_keyword"],
        })
        if len(groups) >= top:
            break
    return groups


def _novelty(vecs: np.ndarray, ages: np.ndarray, recent_frac: float = 0.25) -> dict:
    """Newest finds vs the rest: nearest-neighbour cosine distance.

    Split by recency *rank* (newest `recent_frac` of the corpus vs the older
    remainder), so it works regardless of how old the corpus is in absolute
    days. Each newest vector's distance to its nearest older vector, compared to
    the older set's internal nearest-neighbour baseline: higher -> the engine is
    broadening into new idea space; lower -> it is rehashing existing ideas.
    """
    n = len(ages)
    if n < 8:
        return {"note": "not enough embeddings to measure novelty"}

    order = np.argsort(ages)  # ascending age -> most-recent first
    n_recent = max(2, int(round(n * recent_frac)))
    recent_idx, prior_idx = order[:n_recent], order[n_recent:]
    if len(prior_idx) < 2:
        return {"note": "not enough older finds to measure novelty"}

    r, p = vecs[recent_idx], vecs[prior_idx]
    recent_nn = 1.0 - (r @ p.T).max(axis=1)
    sims = p @ p.T
    np.fill_diagonal(sims, -np.inf)
    prior_nn = 1.0 - sims.max(axis=1)

    recent_med = float(np.median(recent_nn))
    base_med = float(np.median(prior_nn))
    verdict = ("broadening" if recent_med > base_med * 1.1
               else "converging" if recent_med < base_med * 0.9
               else "steady")
    return {
        "recent_count": int(n_recent),
        "older_count": int(len(prior_idx)),
        "recent_nn_distance_median": _round(recent_med),
        "older_baseline_nn_distance_median": _round(base_med),
        "verdict": verdict,
    }


def _store_split(rows, mask) -> dict:
    out: dict[str, int] = {}
    for i, r in enumerate(rows):
        if mask is not None and not mask[i]:
            continue
        s = r["store"] or "?"
        out[s] = out.get(s, 0) + 1
    return out


def _age_hours(ts):
    secs = _age_seconds(ts)
    return None if secs is None else secs / 3600.0


def _age_days(ts):
    secs = _age_seconds(ts)
    return 1e9 if secs is None else secs / 86400.0


def _age_seconds(ts):
    if ts is None:
        return None
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_dt.timezone.utc)
    return (now - ts).total_seconds()


def _iso(ts):
    return ts.isoformat() if ts is not None else None


def _round(x, nd=2):
    return None if x is None else round(float(x), nd)


def _clamp_days(days, lo=1, hi=120):
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 14
    return max(lo, min(hi, days))
