"""Category coverage: genre normalization (pure), deficit-weighted mint budget (pure),
and the per-category kept-app + live-keyword read (DB). DB tests use the rolled-back
pg_conn fixture, so global-aggregate asserts are safe (agf_it starts clean)."""
from sqlalchemy import text

from agf.discovery.coverage import (CategoryCoverage, canonical_category,
                                    category_coverage, mint_budget,
                                    under_floor_categories)
from agf.discovery.taxonomy import IN_SCOPE_CATEGORIES


def test_canonical_category_passthrough_and_aliases():
    assert canonical_category("Health & Fitness") == "Health & Fitness"
    assert canonical_category("Education") == "Education"
    # iOS Apple names fold to the taxonomy vocabulary
    assert canonical_category("Social Networking") == "Social"
    assert canonical_category("Graphics & Design") == "Art & Design"
    assert canonical_category("Book") == "Books"
    # Android Play names fold too
    assert canonical_category("Tools") == "Utilities"
    assert canonical_category("Photography") == "Photo & Video"
    assert canonical_category("Music & Audio") == "Music"
    assert canonical_category("Maps & Navigation") == "Navigation"
    assert canonical_category("Video Players & Editors") == "Video Players"
    assert canonical_category("Books & Reference") == "Books"
    assert canonical_category("Travel & Local") == "Travel"
    assert canonical_category("News & Magazines") == "News"
    # out-of-scope / unknown / empty -> None
    assert canonical_category("Games") is None
    assert canonical_category("Stickers") is None
    assert canonical_category("Kazoo Polishing") is None
    assert canonical_category(None) is None
    assert canonical_category("") is None
    for raw in ("Social Networking", "Tools", "Photography"):
        assert canonical_category(raw) in IN_SCOPE_CATEGORIES


def _cov(rows):  # rows: list[(category, found, live_keywords)]
    return {c: CategoryCoverage(c, f, lk) for c, f, lk in rows}


def _cov4(rows):  # rows: list[(category, found, live_keywords, kept_recent)]
    return {c: CategoryCoverage(c, f, lk, kr) for c, f, lk, kr in rows}


def test_mint_budget_favors_deficit_and_floors_at_one():
    # With zero recent yield anywhere, the whole budget flows through the explore
    # (deficit) lane — the original allocation behavior.
    cov = _cov([
        ("Health & Fitness", 40, 9),  # over K -> deficit 0 -> no slots
        ("Dating", 0, 0),             # deficit 15
        ("Beauty", 10, 2),            # deficit 5
    ])
    budget = mint_budget(cov, K=15, total_slots=20)
    assert "Health & Fitness" not in budget
    assert budget["Dating"] > budget["Beauty"]
    assert budget["Beauty"] >= 1
    assert sum(budget.values()) <= 20


def test_mint_budget_all_covered_returns_empty():
    cov = _cov([("Finance", 20, 8), ("Sports", 16, 6)])
    assert mint_budget(cov, K=15, total_slots=40) == {}


def test_mint_budget_exploit_lane_weights_recent_yield():
    # Fully-covered categories with recent keeper yield get the exploit lane (the
    # inverted-bias fix); deficit categories keep the smaller explore lane.
    cov = _cov4([
        ("Health & Fitness", 40, 9, 90),   # deficit 0, high recent yield
        ("Utilities", 30, 5, 30),          # deficit 0, some recent yield
        ("Dating", 0, 0, 0),               # deficit 15, no yield
        ("Beauty", 10, 2, 0),              # deficit 5, no yield
    ])
    budget = mint_budget(cov, K=15, total_slots=20, explore_share=0.25)
    assert budget["Health & Fitness"] > budget["Utilities"] >= 1
    assert budget["Dating"] >= 1
    assert sum(budget.values()) <= 20
    exploit = budget["Health & Fitness"] + budget["Utilities"]
    explore = budget.get("Dating", 0) + budget.get("Beauty", 0)
    assert exploit > explore


def test_mint_budget_explore_share_edges():
    cov = _cov4([("Health & Fitness", 40, 9, 90), ("Dating", 0, 0, 0)])
    assert mint_budget(cov, K=15, total_slots=20,
                       explore_share=0.0) == {"Health & Fitness": 20}
    assert mint_budget(cov, K=15, total_slots=20,
                       explore_share=1.0) == {"Dating": 20}


def test_mint_budget_empty_lane_rolls_slots_to_the_other():
    # No deficit anywhere -> explore slots roll to the exploit lane, none wasted.
    cov = _cov4([("Health & Fitness", 40, 9, 90)])
    assert mint_budget(cov, K=15, total_slots=20) == {"Health & Fitness": 20}


def test_mint_budget_eligible_restricts_and_reallocates():
    cov = _cov4([
        ("Health & Fitness", 40, 9, 90),
        ("Utilities", 30, 5, 30),
        ("Dating", 0, 0, 0),
    ])
    # Only Utilities has listing material this run: it takes the whole budget.
    assert mint_budget(cov, K=15, total_slots=20,
                       eligible={"Utilities"}) == {"Utilities": 20}
    assert mint_budget(cov, K=15, total_slots=20, eligible=set()) == {}


def test_under_floor_categories_counts_static_taxonomy():
    # Total coverage = dynamic live_keywords + static taxonomy terms. With floor=5:
    #   Dating   0 + 0 static = 0  -> under
    #   Beauty   2 + 3 static = 5  -> NOT under (>= floor)
    #   Finance  8 + 8 static = 16 -> NOT under
    cov = _cov([("Dating", 0, 0), ("Beauty", 10, 2), ("Finance", 20, 8)])
    tax = {"Dating": [], "Beauty": ["a", "b", "c"],
           "Finance": ["a", "b", "c", "d", "e", "f", "g", "h"]}
    assert under_floor_categories(cov, floor=5, taxonomy=tax) == ["Dating"]


def test_under_floor_categories_real_taxonomy_clears_floor():
    # Every real in-scope category ships ~9 taxonomy terms, so at the default floor=5
    # none are under-floor from taxonomy alone (the static sweep IS the backbone).
    cov = _cov([("Dating", 0, 0), ("Beauty", 0, 0)])
    assert under_floor_categories(cov, floor=5) == []


def test_category_coverage_counts_kept_apps_and_live_keywords(pg_conn):
    # Sentinel store_ids so we never collide with stray agf_it apps. Rolled back after.
    pg_conn.execute(text(
        "INSERT INTO public.apps (store, store_id, name, genre) VALUES "
        "('ios','COVT_A','A','Health & Fitness'),"
        "('ios','COVT_B','B','Health & Fitness'),"
        "('android','COVT_C','C','Tools'),"
        "('ios','COVT_D','D','Health & Fitness')"))
    pg_conn.execute(text(
        "INSERT INTO discovery.candidates (page_id, store, store_id, source_keyword,"
        " technique, status) VALUES "
        "('COVT_p1','ios','COVT_A','k','niche_term','kept'),"
        "('COVT_p2','ios','COVT_B','k','niche_term','kept'),"
        "('COVT_p3','android','COVT_C','k','niche_term','kept'),"
        "('COVT_p4','ios','COVT_D','k','niche_term','new')"))  # 'new' -> not counted
    pg_conn.execute(text(
        "INSERT INTO discovery.dynamic_keywords (keyword, source, kind, status) VALUES "
        "('covt hf one','new_release:Health & Fitness','app','trial'),"
        "('covt hf two','frame:Health & Fitness','app','active'),"
        "('covt util one','new_release:Utilities','app','trial'),"
        "('covt retired','new_release:Utilities','app','retired')"))
    cov = category_coverage(pg_conn)
    assert cov["Health & Fitness"].found == 2
    assert cov["Health & Fitness"].live_keywords == 2
    assert cov["Utilities"].found == 1          # Android "Tools" folded in
    assert cov["Utilities"].live_keywords == 1  # retired excluded
    assert cov["Dating"].found == 0 and cov["Dating"].live_keywords == 0
    assert set(cov) == set(IN_SCOPE_CATEGORIES)


def test_category_coverage_kept_recent_windows_by_first_seen(pg_conn):
    pg_conn.execute(text(
        "INSERT INTO public.apps (store, store_id, name, genre) VALUES "
        "('ios','COVR_A','A','Health & Fitness'),"
        "('ios','COVR_B','B','Health & Fitness')"))
    pg_conn.execute(text(
        "INSERT INTO discovery.candidates (page_id, store, store_id, source_keyword,"
        " technique, status, first_seen_at) VALUES "
        "('COVR_p1','ios','COVR_A','k','niche_term','kept', now()),"
        "('COVR_p2','ios','COVR_B','k','niche_term','kept', now() - interval '45 days')"))
    cov = category_coverage(pg_conn)
    assert cov["Health & Fitness"].found == 2         # all-time count unchanged
    assert cov["Health & Fitness"].kept_recent == 1   # 45-day-old keep outside window
    assert cov["Dating"].kept_recent == 0
