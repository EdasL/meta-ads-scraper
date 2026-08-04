import numpy as np
from agf.scoring.content_preference import ContentPreferenceModel, MIN_LIKES


def _unit(v):
    v = np.asarray(v, float); return list(v / np.linalg.norm(v))


def _make(n, base, label_name):
    # n vectors clustered near `base`
    out = []
    for i in range(n):
        v = np.array(base, float) + 0.01 * np.array([i % 3, (i + 1) % 3, (i + 2) % 3])
        out.append((i, f"{label_name}{i}", _unit(v)))
    return out


def test_coldstart_below_min_likes():
    m = ContentPreferenceModel.fit(liked=_make(2, [1, 0, 0], "L"),
                                   disliked=_make(50, [0, 1, 0], "D"))
    assert m.version == "coldstart"


def test_direction_scores_likes_above_dislikes():
    liked = _make(MIN_LIKES, [1, 0, 0], "L")
    disliked = _make(40, [0, 1, 0], "D")
    m = ContentPreferenceModel.fit(liked=liked, disliked=disliked)
    assert m.version == "content-sim-v2"
    like_score = np.mean([m.score(v) for _, _, v in liked])
    dis_score = np.mean([m.score(v) for _, _, v in disliked])
    assert like_score > dis_score
    # p_like monotonic + bounded
    assert 0.0 <= m.p_like(dis_score) < m.p_like(like_score) <= 1.0


def test_why_names_nearest_liked():
    liked = _make(MIN_LIKES, [1, 0, 0], "Like")
    disliked = _make(40, [0, 1, 0], "D")
    m = ContentPreferenceModel.fit(liked=liked, disliked=disliked)
    assert "Like" in m.why(_unit([1, 0, 0]))
