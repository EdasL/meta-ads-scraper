"""Sweep taxonomy guarantees: app-focused breadth, no funnel-phrase layer."""
from agf.discovery import taxonomy


def test_sweep_entries_exclude_funnel_layer():
    layers = {e.layer for e in taxonomy.sweep_entries()}
    assert layers == {"niche_term", "store_fragment"}


def test_sweep_taxonomy_is_wide():
    entries = taxonomy.sweep_entries()
    assert len(entries) >= 250                       # spec: ~250-350 keywords
    assert len({e.keyword for e in entries}) == len(entries)   # no duplicates


def test_all_entries_still_serves_experiment_harness():
    assert len(taxonomy.all_entries(layer="funnel_phrase")) >= 28
