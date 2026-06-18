from agf.scoring.preference import EXPLORE_EVERY, PreferenceModel, interleave, why_string


def test_interleave_ratio_and_rank():
    scored = [{"advertiser_id": i, "p_like": 0.9 - i * 0.01,
               "uncertainty": 0.01} for i in range(8)]
    # Make two items clearly the most uncertain.
    scored[6]["uncertainty"] = 0.99
    scored[7]["uncertainty"] = 0.98
    out = interleave(scored)
    assert len(out) == 8
    assert [r["queue_rank"] for r in out] == list(range(8))
    # 4th slot (index 3) is the first explore pick = the most uncertain item.
    assert out[EXPLORE_EVERY - 1]["advertiser_id"] == 6
    # Every advertiser appears exactly once.
    assert sorted(r["advertiser_id"] for r in out) == list(range(8))


def test_why_string_heuristic_names_funnel():
    m = PreferenceModel._heuristic()
    s = why_string(m, {"has_funnel": True, "whitespace": 0.9})
    assert "has funnel" in s
