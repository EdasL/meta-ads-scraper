"""Pure discovery-saturation metrics: category diversity (Shannon entropy, normalized
entropy, Herfindahl/HHI) and per-keyword novelty rate. No DB — these are the testable
core; scripts/discovery_metrics.py wires them to live counts."""
import math

from agf.discovery.metrics import (herfindahl, normalized_entropy, novelty_rate,
                                    shannon_entropy)


def test_shannon_entropy_uniform_two_is_one_bit():
    assert shannon_entropy({"a": 1, "b": 1}) == 1.0          # log2(2) bits


def test_shannon_entropy_single_category_is_zero():
    assert shannon_entropy({"a": 5}) == 0.0
    assert shannon_entropy({}) == 0.0


def test_shannon_entropy_skewed():
    # -(0.75 log2 0.75 + 0.25 log2 0.25) ~= 0.8113
    assert math.isclose(shannon_entropy({"a": 3, "b": 1}), 0.8112781, rel_tol=1e-6)


def test_herfindahl_concentration():
    assert herfindahl({"a": 1, "b": 1}) == 0.5               # two equal shares
    assert herfindahl({"a": 5}) == 1.0                       # fully concentrated
    assert math.isclose(herfindahl({"a": 3, "b": 1}), 0.625)
    assert herfindahl({}) == 0.0


def test_normalized_entropy_is_zero_to_one():
    assert normalized_entropy({"a": 1, "b": 1}) == 1.0       # perfectly spread
    assert normalized_entropy({"a": 5}) == 0.0               # single bucket → 0
    assert normalized_entropy({}) == 0.0
    assert 0.0 < normalized_entropy({"a": 3, "b": 1}) < 1.0


def test_novelty_rate():
    assert novelty_rate(2, 10) == 0.2
    assert novelty_rate(0, 10) == 0.0
    assert novelty_rate(5, 0) == 0.0                         # nothing returned → 0, no div0
    assert novelty_rate(10, 10) == 1.0
