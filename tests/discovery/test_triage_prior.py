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


def test_crowding_none_is_byte_identical():
    # crowding_by_id=None must reproduce today's ordering exactly (pinned fixture
    # captured from the pre-crowding implementation).
    fifo = [20, 21, 22, 23, 24, 25, 26, 27]
    d = np.array([1.0, 0.0])
    emb = {a: [(a - 19) / 10.0, 0.5] for a in fifo}
    assert prioritize(fifo, emb, d, explore_frac=0.25, crowding_by_id=None) == \
        [27, 26, 25, 20, 24, 23, 22, 21]


def test_crowded_candidate_demoted_below_equal_taste_uncrowded():
    # Two candidates with identical taste alignment; the crowded one sinks.
    fifo = [1, 2]
    d = np.array([1.0, 0.0])
    emb = {1: [1.0, 0.0], 2: [1.0, 0.0]}
    out = prioritize(fifo, emb, d, explore_frac=0.0,
                     crowding_by_id={1: 0.9}, tail=0.80, penalty=0.5)
    assert out == [2, 1]


def test_missing_or_below_tail_id_gets_no_penalty():
    # id absent from the dict, or present but below tail, keeps its taste score.
    fifo = [1, 2]
    d = np.array([1.0, 0.0])
    emb = {1: [1.0, 0.0], 2: [0.9, 0.0]}          # 1 is the stronger match
    for crowding in ({}, {99: 0.99}, {1: 0.5}):
        out = prioritize(fifo, emb, d, explore_frac=0.0,
                         crowding_by_id=crowding, tail=0.80, penalty=0.5)
        assert out[0] == 1


def test_explore_lane_blind_to_crowding():
    # The explore reserve surfaces the oldest FIFO id regardless of its crowding.
    fifo = [10, 11, 12, 13, 14, 15, 16, 17]      # oldest-first
    d = np.array([1.0, 0.0])
    emb = {a: [(a - 9) / 10.0, 0.5] for a in fifo}
    out = prioritize(fifo, emb, d, explore_frac=0.25,
                     crowding_by_id={10: 0.99}, tail=0.80, penalty=0.5)
    assert set(out) == set(fifo)
    assert 10 in out[:4]                          # explore still surfaces the crowded oldest
