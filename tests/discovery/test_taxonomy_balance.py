"""The frame can't silently re-skew: every in-scope category present, even quota,
no dupes, app-suffixed, no brand/ad-copy noise. Pure (no DB)."""
from agf.discovery import taxonomy

# A few obvious brand tokens a generic niche term should never contain.
_BRAND_TOKENS = {"calm", "duolingo", "noom", "spotify", "tinder", "headspace", "strava"}
# Generic ad-copy phrases that are not niches.
_GENERIC = {"what's your goal", "try the app free", "download the app", "best app"}


def test_every_in_scope_category_present_and_no_others():
    assert set(taxonomy.NICHE_TERMS_BY_CATEGORY) == set(taxonomy.IN_SCOPE_CATEGORIES)
    assert "Games" not in taxonomy.NICHE_TERMS_BY_CATEGORY
    assert "Stickers" not in taxonomy.NICHE_TERMS_BY_CATEGORY


def test_each_category_within_quota_band():
    lo, hi = taxonomy.NICHE_PER_CATEGORY_MIN, taxonomy.NICHE_PER_CATEGORY_MAX
    for cat, terms in taxonomy.NICHE_TERMS_BY_CATEGORY.items():
        assert lo <= len(terms) <= hi, f"{cat} has {len(terms)} terms (band {lo}-{hi})"


def test_no_duplicate_terms_across_frame():
    flat = taxonomy.NICHE_TERMS
    assert len(flat) == len(set(flat)), "duplicate niche terms"


def test_terms_are_clean_and_app_suffixed():
    for t in taxonomy.NICHE_TERMS:
        assert t == t.lower(), f"{t!r} not lowercase"
        assert t.endswith(" app"), f"{t!r} must end in 'app'"
        assert t not in _GENERIC, f"{t!r} is a generic ad-copy phrase"
        toks = set(t.split())
        assert not (toks & _BRAND_TOKENS), f"{t!r} contains a brand name"


def test_niche_terms_is_flatten_of_the_dict():
    expected = [t for terms in taxonomy.NICHE_TERMS_BY_CATEGORY.values() for t in terms]
    assert taxonomy.NICHE_TERMS == expected
