"""Discovery-saturation metrics (pure). Measure whether the discovery surface is
collapsing into a few categories or staying broad, and which keywords have gone dry.

- shannon_entropy / normalized_entropy: category-histogram DIVERSITY (high = spread).
- herfindahl: category CONCENTRATION (HHI; high = collapsed into few basins).
- novelty_rate: a keyword's new-unique ÷ returned; below a floor it's SATURATED.

The saturation alarm (research §6): rising HHI + decayed novelty + flywheel yield→0 means
"broaden," not "scrape harder." scripts/discovery_metrics.py feeds these live counts."""
import math


def _shares(counts: dict) -> list[float]:
    total = sum(v for v in counts.values() if v > 0)
    if total <= 0:
        return []
    return [v / total for v in counts.values() if v > 0]


def shannon_entropy(counts: dict) -> float:
    """Shannon entropy of a category histogram, in BITS (log2). 0 = one category,
    log2(k) = k equally-populated categories. Empty/single → 0."""
    return -sum(p * math.log2(p) for p in _shares(counts))


def normalized_entropy(counts: dict) -> float:
    """Shannon entropy scaled to [0, 1] by dividing by log2(#non-empty categories):
    1.0 = perfectly even spread, 0.0 = fully concentrated (or <2 categories)."""
    shares = _shares(counts)
    if len(shares) < 2:
        return 0.0
    return (-sum(p * math.log2(p) for p in shares)) / math.log2(len(shares))


def herfindahl(counts: dict) -> float:
    """Herfindahl-Hirschman Index: sum of squared shares, in [0, 1]. 1.0 = a single
    category owns everything; → 1/k as the k categories even out. The inverse view of
    entropy — rising HHI is the collapse alarm."""
    return sum(p * p for p in _shares(counts))


def novelty_rate(new_unique: int, returned: int) -> float:
    """Fraction of a keyword's (or basin's) returned items that were genuinely new.
    Returns 0.0 when nothing was returned (no division by zero). Below a floor
    (~0.05) the keyword is saturated and its budget should move to exploration."""
    if returned <= 0:
        return 0.0
    return new_unique / returned
