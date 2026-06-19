import numpy as np
from agf.scoring.content_preference import ContentPreferenceModel, MIN_LIKES


def _unit(v):
    v = np.asarray(v, float); return list(v / np.linalg.norm(v))


def _make(n, base, name):
    return [(i, f"{name}{i}", _unit(np.array(base, float) + 0.01 * (i % 3)))
            for i in range(n)]


def test_direction_none_in_coldstart():
    m = ContentPreferenceModel.fit(liked=_make(2, [1, 0, 0], "L"),
                                   disliked=_make(20, [0, 1, 0], "D"))
    assert m.version == "coldstart"
    assert m.direction is None


def test_direction_is_unit_vector_when_fit():
    m = ContentPreferenceModel.fit(liked=_make(MIN_LIKES, [1, 0, 0], "L"),
                                   disliked=_make(20, [0, 1, 0], "D"))
    assert m.direction is not None
    assert abs(float(np.linalg.norm(m.direction)) - 1.0) < 1e-6
