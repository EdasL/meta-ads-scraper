import numpy as np

from agf.discovery.crowding import crowding_scores, crowding_scores_self


def test_identical_vectors_crowd_to_one():
    corpus = np.ones((5, 3), dtype=np.float32)
    scores = crowding_scores(np.ones((1, 3), dtype=np.float32), corpus, k=3)
    assert scores.shape == (1,)
    assert np.isclose(scores[0], 1.0, atol=1e-5)


def test_orthogonal_one_hot_crowd_to_zero():
    corpus = np.eye(4, dtype=np.float32)          # mutually orthogonal
    v = np.array([[1.0, 0.0, 0.0, 0.0]], dtype=np.float32)
    # v matches corpus row 0 exactly (sim 1) but is orthogonal to the other 3 (sim 0);
    # against a corpus WITHOUT itself, its neighbors are all orthogonal -> 0.
    corpus_without_self = corpus[1:]
    scores = crowding_scores(v, corpus_without_self, k=3)
    assert np.isclose(scores[0], 0.0, atol=1e-5)


def test_k_greater_than_corpus_uses_available():
    corpus = np.ones((2, 3), dtype=np.float32)
    scores = crowding_scores(np.ones((1, 3), dtype=np.float32), corpus, k=10)
    assert np.isclose(scores[0], 1.0, atol=1e-5)


def test_empty_corpus_is_nan():
    scores = crowding_scores(np.ones((3, 3), dtype=np.float32), np.zeros((0, 3), dtype=np.float32))
    assert scores.shape == (3,)
    assert np.all(np.isnan(scores))


def test_zero_vector_row_does_not_crash():
    corpus = np.ones((4, 3), dtype=np.float32)
    v = np.zeros((1, 3), dtype=np.float32)
    scores = crowding_scores(v, corpus, k=2)
    assert scores.shape == (1,)
    assert np.isclose(scores[0], 0.0, atol=1e-5)   # zero vector: cos-sim 0 to all


def test_self_leave_one_out_excludes_self():
    # two near-identical rows + one orthogonal loner
    corpus = np.array(
        [[1.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32
    )
    scores = crowding_scores_self(corpus, k=1)
    # the duplicate pair scores 1.0 against each OTHER (self diagonal masked)
    assert np.isclose(scores[0], 1.0, atol=1e-5)
    assert np.isclose(scores[1], 1.0, atol=1e-5)
    # the loner's nearest neighbor is orthogonal -> 0
    assert np.isclose(scores[2], 0.0, atol=1e-5)


def test_self_zero_and_one_row_corpus_are_nan():
    assert np.all(np.isnan(crowding_scores_self(np.zeros((0, 3), dtype=np.float32))))
    one = crowding_scores_self(np.ones((1, 3), dtype=np.float32))
    assert one.shape == (1,)
    assert np.all(np.isnan(one))


def test_self_zero_vector_row_does_not_crash():
    corpus = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 0.0]], dtype=np.float32)
    scores = crowding_scores_self(corpus, k=1)
    assert scores.shape == (3,)
    assert not np.isnan(scores[1])                  # real rows still scored
