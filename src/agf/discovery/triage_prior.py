"""Reorder the kept-candidate enrich queue by cosine to the user's liked direction,
keeping an epsilon-greedy explore reserve (oldest-first, taste-blind) so the budget
never tunnels on one niche. Pure — no DB. Advisory: callers fall back to FIFO."""
from __future__ import annotations

import numpy as np

from agf.scoring.content_preference import ContentPreferenceModel


def liked_direction(liked, disliked):
    """liked/disliked: list[(id, name, unit_vec)] -> unit direction or None (cold-start)."""
    return ContentPreferenceModel.fit(liked, disliked).direction


def prioritize(fifo_order, emb_by_id, direction, explore_frac=0.25):
    """fifo_order = advertiser ids oldest-first. Returns a reordering: priority slots
    by cosine(emb, direction) desc, with ~explore_frac of slots filled oldest-first.
    direction None / no embeddings -> fifo_order unchanged. Nothing is dropped."""
    if direction is None or not emb_by_id:
        return list(fifo_order)
    d = np.asarray(direction, float)

    def score(a):
        v = emb_by_id.get(a)
        return float(np.dot(np.asarray(v, float), d)) if v is not None else float("-inf")

    priority = sorted(fifo_order, key=score, reverse=True)
    explore_every = max(2, round(1 / explore_frac)) if explore_frac > 0 else 0
    out, seen = [], set()
    pi = fi = 0

    def _nxt(lst, i):
        while i < len(lst) and lst[i] in seen:
            i += 1
        return i

    while len(out) < len(fifo_order):
        use_explore = explore_every and (len(out) % explore_every == explore_every - 1)
        pick = None
        if use_explore:
            fi = _nxt(fifo_order, fi)
            if fi < len(fifo_order):
                pick = fifo_order[fi]
        if pick is None:
            pi = _nxt(priority, pi)
            if pi < len(priority):
                pick = priority[pi]
        if pick is None:                       # priority exhausted -> remaining FIFO
            fi = _nxt(fifo_order, fi)
            pick = fifo_order[fi]
        seen.add(pick)
        out.append(pick)
    return out
