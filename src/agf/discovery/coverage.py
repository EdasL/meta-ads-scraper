"""Category coverage of the discovery frame: normalize store genres to the taxonomy's
canonical vocabulary, read per-category kept-app counts + live keyword counts, and
allocate mint budget toward under-covered categories. Taste-blind (no ratings read).

See docs/superpowers/specs/2026-07-02-keyword-minting-frame-design.md."""
from dataclasses import dataclass

from sqlalchemy import text

from agf.discovery.taxonomy import IN_SCOPE_CATEGORIES, NICHE_TERMS_BY_CATEGORY

# Genres we never mint into (Games are vetoed product-wide; Stickers aren't standalone apps).
OUT_OF_SCOPE_GENRES = frozenset({"Games", "Stickers"})

# Raw store genre (iOS primaryGenreName / Android Play category) -> taxonomy canonical.
# Grounded in the live genre distribution (2026-07-02). Names already equal to a member of
# IN_SCOPE_CATEGORIES need no entry — canonical_category passes them through.
CATEGORY_ALIASES: dict[str, str] = {
    # iOS Apple names
    "Social Networking": "Social",
    "Graphics & Design": "Art & Design",
    "Book": "Books",
    # Android Play names
    "Tools": "Utilities",
    "Photography": "Photo & Video",
    "Music & Audio": "Music",
    "Maps & Navigation": "Navigation",
    "Video Players & Editors": "Video Players",
    "Books & Reference": "Books",
    "Travel & Local": "Travel",
    "News & Magazines": "News",
    "Communication": "Communication",  # already in-scope; explicit for clarity
}


def canonical_category(genre: str | None) -> str | None:
    """Fold a raw store genre into the taxonomy's IN_SCOPE_CATEGORIES vocabulary.
    Returns None for out-of-scope, unmapped, or empty input."""
    if not genre:
        return None
    if genre in OUT_OF_SCOPE_GENRES:
        return None
    mapped = CATEGORY_ALIASES.get(genre, genre)
    return mapped if mapped in IN_SCOPE_CATEGORIES else None


@dataclass
class CategoryCoverage:
    category: str
    found: int           # distinct kept apps in this canonical category (all-time)
    live_keywords: int   # trial+active dynamic keywords sourced to this category
    kept_recent: int = 0  # distinct kept apps first seen inside the recent window


def _proportional(weights: dict[str, int], slots: int) -> dict[str, int]:
    """Split `slots` across categories proportional to positive `weights`. Every weighted
    category gets at least one slot; when the floored shares overflow, the largest
    weights win (deterministic: -weight, then name)."""
    if not weights or slots <= 0:
        return {}
    total = sum(weights.values())
    raw = {c: max(1, (w * slots) // total) for c, w in weights.items()}
    if sum(raw.values()) <= slots:
        return raw
    order = sorted(weights, key=lambda c: (-weights[c], c))
    out, spent = {}, 0
    for c in order:
        if spent >= slots:
            break
        take = min(raw[c], slots - spent)
        if take >= 1:
            out[c] = take
            spent += take
    return out


def mint_budget(coverage: dict[str, CategoryCoverage], *, K: int, total_slots: int,
                explore_share: float = 0.25,
                eligible: set[str] | None = None) -> dict[str, int]:
    """Allocate total_slots across categories in two lanes:

    - EXPLOIT (1 - explore_share): proportional to kept_recent — categories that are
      actually producing keepers on Meta right now, regardless of coverage.
    - EXPLORE (explore_share): proportional to coverage deficit (max(0, K - found)) —
      the original rule, kept as a bounded probe of under-covered categories.

    A lane with no positive weights rolls its slots to the other, so nothing is wasted.
    `eligible` (None = all) restricts both lanes to categories the caller can actually
    mint into this run (i.e. has listing material for). Deficit-only allocation is
    inverted in practice — it starves every proven-fertile category (found >= K) and
    sends the whole budget to Meta deserts; see the 2026-07-06 spec."""
    cats = [c for c in coverage if eligible is None or c in eligible]
    deficits = {c: max(0, K - coverage[c].found) for c in cats}
    deficits = {c: d for c, d in deficits.items() if d > 0}
    yields_ = {c: coverage[c].kept_recent for c in cats if coverage[c].kept_recent > 0}
    if total_slots <= 0 or (not deficits and not yields_):
        return {}
    explore_slots = round(explore_share * total_slots)
    exploit_slots = total_slots - explore_slots
    if not yields_:
        explore_slots, exploit_slots = explore_slots + exploit_slots, 0
    elif not deficits:
        exploit_slots, explore_slots = exploit_slots + explore_slots, 0
    out: dict[str, int] = {}
    for lane in (_proportional(yields_, exploit_slots),
                 _proportional(deficits, explore_slots)):
        for c, s in lane.items():
            out[c] = out.get(c, 0) + s
    return out


def under_floor_categories(coverage: dict[str, CategoryCoverage], *, floor: int,
                           taxonomy: dict[str, list] = NICHE_TERMS_BY_CATEGORY) -> list[str]:
    """Categories whose TOTAL live coverage is below the floor, scarcest first. Total
    coverage = the static taxonomy keywords already swept daily (the permanent frame
    backbone) PLUS the dynamic minted/frame keywords. Counting the static taxonomy is
    what stops the frame-seeder from redundantly re-adding the ~8-12 niche_terms per
    category that sweep_entries() already sweeps every day — in steady state every
    category with taxonomy entries clears the floor and is left alone. `taxonomy` is
    injectable for tests."""
    def total(cov: CategoryCoverage) -> int:
        return cov.live_keywords + len(taxonomy.get(cov.category, ()))
    below = [cov for cov in coverage.values() if total(cov) < floor]
    below.sort(key=lambda cov: (total(cov), cov.category))
    return [cov.category for cov in below]


def category_coverage(conn, *, window_days: int = 30) -> dict[str, CategoryCoverage]:
    """Per in-scope category: distinct kept apps found (all-time + recent window) and
    live (trial/active) minted/frame keywords. Read-only. Categories with no data still
    appear (found=0, live=0, kept_recent=0)."""
    found: dict[str, int] = {c: 0 for c in IN_SCOPE_CATEGORIES}
    recent: dict[str, int] = {c: 0 for c in IN_SCOPE_CATEGORIES}
    live: dict[str, int] = {c: 0 for c in IN_SCOPE_CATEGORIES}

    app_rows = conn.execute(text(
        "SELECT a.genre AS genre, count(DISTINCT a.id) AS n "
        "FROM discovery.candidates c "
        "JOIN public.apps a ON a.store = c.store AND a.store_id = c.store_id "
        "WHERE c.status = 'kept' AND a.genre IS NOT NULL "
        "GROUP BY a.genre")).all()
    for r in app_rows:
        cat = canonical_category(r.genre)
        if cat is not None:
            found[cat] += r.n

    recent_rows = conn.execute(text(
        "SELECT a.genre AS genre, count(DISTINCT a.id) AS n "
        "FROM discovery.candidates c "
        "JOIN public.apps a ON a.store = c.store AND a.store_id = c.store_id "
        "WHERE c.status = 'kept' AND a.genre IS NOT NULL "
        "AND c.first_seen_at >= now() - make_interval(days => :days) "
        "GROUP BY a.genre"), {"days": window_days}).all()
    for r in recent_rows:
        cat = canonical_category(r.genre)
        if cat is not None:
            recent[cat] += r.n

    kw_rows = conn.execute(text(
        "SELECT source, count(*) AS n FROM discovery.dynamic_keywords "
        "WHERE status IN ('trial','active') "
        "AND (source LIKE 'new_release:%' OR source LIKE 'frame:%') "
        "GROUP BY source")).all()
    for r in kw_rows:
        cat = r.source.split(":", 1)[1] if ":" in r.source else None
        if cat in live:
            live[cat] += r.n

    return {c: CategoryCoverage(c, found[c], live[c], recent[c])
            for c in IN_SCOPE_CATEGORIES}
