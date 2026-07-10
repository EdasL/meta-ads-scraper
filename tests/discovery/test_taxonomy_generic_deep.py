"""Shape of the generic-deep copy-phrase lane (spec 2026-07-10): founder-approved
19 terms, no duplicates against any daily-swept layer, weekly-only (not in
sweep_entries), correct search types."""
from agf.discovery.taxonomy import (FUNNEL_PHRASES, GENERIC_DEEP_PHRASES,
                                    GENERIC_DEEP_TERMS, GENERIC_DEEP_UNORDERED,
                                    NICHE_TERMS, STORE_FRAGMENTS,
                                    generic_deep_entries, sweep_entries)


def test_seed_list_is_the_approved_19():
    assert len(GENERIC_DEEP_TERMS) == 19
    assert len(set(GENERIC_DEEP_TERMS)) == 19                    # no dupes inside
    assert GENERIC_DEEP_UNORDERED == ["app", "ai"]


def test_no_overlap_with_daily_layers():
    daily = set(FUNNEL_PHRASES) | set(NICHE_TERMS) | set(STORE_FRAGMENTS)
    assert not (set(GENERIC_DEEP_TERMS) & daily)


def test_entries_shape_and_search_types():
    entries = generic_deep_entries(150)
    assert len(entries) == 19
    assert all(e.layer == "generic_deep" for e in entries)
    assert all(e.max_pages == 150 for e in entries)
    by_kw = {e.keyword: e.search_type for e in entries}
    assert by_kw["app"] == "KEYWORD_UNORDERED"
    assert by_kw["ai"] == "KEYWORD_UNORDERED"
    assert by_kw["ai headshots"] == "KEYWORD_EXACT_PHRASE"
    assert by_kw["wish i knew sooner"] == "KEYWORD_EXACT_PHRASE"


def test_not_in_the_daily_sweep():
    daily_kws = {e.keyword for e in sweep_entries()}
    assert not (set(GENERIC_DEEP_TERMS) & daily_kws)
