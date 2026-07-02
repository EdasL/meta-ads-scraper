"""Category coverage of the discovery frame: normalize store genres to the taxonomy's
canonical vocabulary, read per-category kept-app counts + live keyword counts, and
allocate mint budget toward under-covered categories. Taste-blind (no ratings read).

See docs/superpowers/specs/2026-07-02-keyword-minting-frame-design.md."""
from dataclasses import dataclass

from sqlalchemy import text

from agf.discovery.taxonomy import IN_SCOPE_CATEGORIES

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
    found: int          # distinct kept apps in this canonical category
    live_keywords: int  # trial+active dynamic keywords sourced to this category


def mint_budget(coverage: dict[str, CategoryCoverage], *, K: int,
                total_slots: int) -> dict[str, int]:
    """Allocate total_slots across categories proportional to their coverage deficit
    (max(0, K - found)). Every category with a positive deficit gets at least one slot;
    fully-covered categories get none. When the floored shares overflow total_slots, the
    largest deficits win (deterministic: -deficit, then name)."""
    deficits = {c: max(0, K - cov.found) for c, cov in coverage.items()}
    deficits = {c: d for c, d in deficits.items() if d > 0}
    if not deficits or total_slots <= 0:
        return {}
    total_deficit = sum(deficits.values())
    raw = {c: max(1, (d * total_slots) // total_deficit) for c, d in deficits.items()}
    if sum(raw.values()) <= total_slots:
        return raw
    order = sorted(deficits, key=lambda c: (-deficits[c], c))
    out, spent = {}, 0
    for c in order:
        if spent >= total_slots:
            break
        take = min(raw[c], total_slots - spent)
        if take >= 1:
            out[c] = take
            spent += take
    return out


def under_floor_categories(coverage: dict[str, CategoryCoverage], *,
                           floor: int) -> list[str]:
    """Categories whose live keyword count is below the floor, scarcest first."""
    below = [cov for cov in coverage.values() if cov.live_keywords < floor]
    below.sort(key=lambda cov: (cov.live_keywords, cov.category))
    return [cov.category for cov in below]


def category_coverage(conn) -> dict[str, CategoryCoverage]:
    """Per in-scope category: distinct kept apps found + live (trial/active) minted/frame
    keywords. Read-only. Categories with no data still appear (found=0, live=0)."""
    found: dict[str, int] = {c: 0 for c in IN_SCOPE_CATEGORIES}
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

    kw_rows = conn.execute(text(
        "SELECT source, count(*) AS n FROM discovery.dynamic_keywords "
        "WHERE status IN ('trial','active') "
        "AND (source LIKE 'new_release:%' OR source LIKE 'frame:%') "
        "GROUP BY source")).all()
    for r in kw_rows:
        cat = r.source.split(":", 1)[1] if ":" in r.source else None
        if cat in live:
            live[cat] += r.n

    return {c: CategoryCoverage(c, found[c], live[c]) for c in IN_SCOPE_CATEGORIES}
