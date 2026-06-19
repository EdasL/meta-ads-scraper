import numpy as np
from agf.discovery.triage_prior import prioritize, liked_direction


def test_passthrough_when_no_direction():
    order = [1, 2, 3, 4]
    assert prioritize(order, {1: [1, 0]}, None) == order


def test_high_cosine_leads_and_explore_reserve_present():
    fifo = [10, 11, 12, 13, 14, 15, 16, 17]      # oldest-first
    d = np.array([1.0, 0.0])
    emb = {a: [(a - 9) / 10.0, 0.5] for a in fifo}   # 17 most aligned, 10 least
    out = prioritize(fifo, emb, d, explore_frac=0.25)
    assert set(out) == set(fifo)                  # nothing dropped
    assert out[0] == 17                           # top priority leads
    assert 10 in out[:5]                          # oldest FIFO surfaces via explore reserve


def test_no_embedding_sinks_but_not_dropped():
    out = prioritize([1, 2, 3], {2: [1.0, 0.0]}, np.array([1.0, 0.0]), explore_frac=0.0)
    assert out[0] == 2 and set(out) == {1, 2, 3}


def test_deterministic():
    fifo = [5, 6, 7, 8]
    d = np.array([1.0, 0.0])
    emb = {a: [a / 10.0, 0.0] for a in fifo}
    assert prioritize(fifo, emb, d) == prioritize(fifo, emb, d)


def test_liked_direction_coldstart_none():
    liked = [(i, f"L{i}", [1.0, 0.0]) for i in range(2)]
    disliked = [(i, f"D{i}", [0.0, 1.0]) for i in range(10)]
    assert liked_direction(liked, disliked) is None
