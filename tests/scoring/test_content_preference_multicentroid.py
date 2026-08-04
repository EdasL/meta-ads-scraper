import numpy as np

from agf.scoring.content_preference import ContentPreferenceModel, _cluster


def _unit(v):
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


def _mk(n, base, name, start_id=0):
    """n near-duplicates of base with a tiny per-item wiggle, ids ascending."""
    out = []
    for i in range(n):
        v = np.asarray(base, float).copy()
        v[2] += 0.05 * i
        out.append((start_id + i, f"{name}{i}", _unit(v)))
    return out


def _two_cluster_likes():
    # Two well-separated like clusters: near e1 and near e2 (dim 4).
    a = _mk(12, [1.0, 0.0, 0.05, 0.0], "A", start_id=100)
    b = _mk(12, [0.0, 1.0, 0.05, 0.0], "B", start_id=200)
    return a + b


def test_cluster_deterministic_and_separates():
    likes = _two_cluster_likes()
    vecs = [v for _, _, v in likes]
    assign = _cluster(vecs, 2)
    # First 12 in one cluster, last 12 in the other.
    assert len(set(assign[:12])) == 1
    assert len(set(assign[12:])) == 1
    assert assign[0] != assign[12]
    # Deterministic: same input, same result.
    assert assign == _cluster(vecs, 2)


def test_multicentroid_scores_both_clusters_high():
    likes = _two_cluster_likes()
    dislikes = _mk(2, [0.0, 0.0, 0.0, 1.0], "D", start_id=900)
    m = ContentPreferenceModel.fit(likes, dislikes)
    assert m.version == "content-sim-v2"
    pa = m.p_like(m.score(_unit([1, 0, 0, 0])))
    pb = m.p_like(m.score(_unit([0, 1, 0, 0])))
    # v1 (single mean direction) scores the e2 cluster's probe at ~the midpoint;
    # v2 must score BOTH cluster probes clearly likeable.
    assert pa > 0.8
    assert pb > 0.8
    # Off-taste probe stays low.
    assert m.p_like(m.score(_unit([0, 0, 0, 1]))) < 0.5


def test_fit_order_invariant_after_id_sort():
    likes = _two_cluster_likes()
    dislikes = _mk(2, [0.0, 0.0, 0.0, 1.0], "D", start_id=900)
    m1 = ContentPreferenceModel.fit(likes, dislikes)
    m2 = ContentPreferenceModel.fit(list(reversed(likes)), dislikes)
    probe = _unit([0.5, 0.5, 0, 0])
    assert abs(m1.score(probe) - m2.score(probe)) < 1e-12


def test_neutrals_pull_negative_centroid():
    likes = _mk(12, [1.0, 0.0, 0.05, 0.0], "A", start_id=100)
    neut = _mk(6, [0.0, 1.0, 0.05, 0.0], "N", start_id=500)
    m_plain = ContentPreferenceModel.fit(likes, [], likes_per_centroid=12,
                                         max_centroids=1)
    m_neut = ContentPreferenceModel.fit(likes, [], neutral=neut,
                                        likes_per_centroid=12, max_centroids=1,
                                        neutral_weight=0.25)
    probe = _unit([0, 1, 0, 0])   # looks like the neutral'd cluster
    assert m_neut.score(probe) < m_plain.score(probe)


def test_neutrals_excluded_from_platt_labels():
    # Fitting with neutrals must not add 0-labels: same likes/dislikes with and
    # without neutrals of IDENTICAL vectors to the dislikes leaves calibration
    # input size unchanged (we assert indirectly: p_like of a strong like-probe
    # stays high — neutral flooding must not crush calibration).
    likes = _mk(12, [1.0, 0.0, 0.05, 0.0], "A", start_id=100)
    dis = _mk(2, [0.0, 0.0, 0.0, 1.0], "D", start_id=900)
    neut = _mk(40, [0.0, 0.9, 0.1, 0.0], "N", start_id=1000)
    m = ContentPreferenceModel.fit(likes, dis, neutral=neut)
    assert m.p_like(m.score(_unit([1, 0, 0, 0]))) > 0.8


def test_k_policy_clamps():
    likes = _mk(12, [1.0, 0.0, 0.05, 0.0], "A", start_id=100)
    # 12 likes / 12-per-centroid = 1 cluster; behavior = v1 + version bump.
    m = ContentPreferenceModel.fit(likes, [], likes_per_centroid=12,
                                   max_centroids=4)
    assert m.version == "content-sim-v2"
    assert m.direction is not None
    # direction (global) and the single cluster dir agree.
    assert abs(m.score(m.direction) - float(np.dot(m.direction, m.direction))) < 1e-9


def test_structure_guard_collapses_blob():
    # One blob forced into k=4: every split direction nearly coincides, so the
    # guard must fall back to the single global direction (identical scores to
    # an explicit K=1 fit).
    likes = _mk(12, [1.0, 0.0, 0.05, 0.0], "A", start_id=100)
    dis = _mk(2, [0.0, 0.0, 0.0, 1.0], "D", start_id=900)
    m_multi = ContentPreferenceModel.fit(likes, dis, likes_per_centroid=1,
                                         max_centroids=4, split_max_cos=0.85)
    m_single = ContentPreferenceModel.fit(likes, dis, likes_per_centroid=12,
                                          max_centroids=1)
    for probe in ([1, 0, 0, 0], [0, 1, 0, 0], [0.5, 0.5, 0.5, 0]):
        v = _unit(probe)
        assert abs(m_multi.score(v) - m_single.score(v)) < 1e-12


def test_structure_guard_keeps_real_clusters():
    # Genuinely separated clusters stay split: scores must NOT equal the
    # single-direction fit for a probe aligned with the second cluster.
    likes = _two_cluster_likes()
    dis = _mk(2, [0.0, 0.0, 0.0, 1.0], "D", start_id=900)
    m = ContentPreferenceModel.fit(likes, dis, likes_per_centroid=12,
                                   max_centroids=4, split_max_cos=0.85)
    m1 = ContentPreferenceModel.fit(likes, dis, likes_per_centroid=24,
                                    max_centroids=1)
    probe = _unit([0, 1, 0, 0])
    assert m.score(probe) > m1.score(probe) + 0.05


def test_coldstart_unchanged():
    likes = _mk(3, [1.0, 0.0, 0.05, 0.0], "A")
    m = ContentPreferenceModel.fit(likes, [])
    assert m.version == "coldstart"
    assert m.score([1, 0, 0, 0]) == 0.0
    assert m.direction is None
