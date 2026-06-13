"""The flywheel's pure miner: cross-advertiser phrase recurrence, normalization,
and the exclusion filters (taxonomy/tried containment, generic ad-speak, stopword
edges). No DB, no network."""
from agf.discovery.keyword_mining import mine_phrases, phrases_of


def test_cross_advertiser_recurrence_beats_one_brands_slogan():
    corpus = [
        (1, "Track your habits with streak tracking and an AI coach."),
        (2, "Streak tracking that finally sticks. Try the AI coach."),
        (3, "ZorbloFit forever! ZorbloFit forever! ZorbloFit forever!"),  # one brand
    ]
    mined = mine_phrases(corpus, min_advertisers=2, limit=5)
    assert "streak tracking" in mined
    assert "ai coach" in mined
    assert not any("zorblofit" in p for p in mined)   # 1 advertiser < min_advertisers


def test_exclusion_is_containment_both_ways():
    corpus = [(1, "The best habit tracker for busy people."),
              (2, "A habit tracker you will keep using.")]
    # "habit tracker app" is already in the taxonomy -> "habit tracker" must not re-mint
    assert mine_phrases(corpus, exclude={"habit tracker app"}) == []


def test_generic_ad_speak_and_stopword_edges_are_dropped():
    corpus = [(1, "Learn more today. Download now and get started with the plan."),
              (2, "Learn more! Download now. Get started right away with the plan.")]
    mined = mine_phrases(corpus, min_advertisers=2)
    assert "learn more" not in mined
    assert "download now" not in mined
    assert "get started" not in mined
    assert not any(p.startswith(("the ", "with ")) or p.endswith(" with") for p in mined)


def test_normalization_strips_templates_urls_emoji():
    grams = phrases_of("{{product.brand}} is great 🐶 see https://x.co/y wall pilates plan")
    assert "wall pilates plan" in grams
    assert not any("product" in g or "http" in g for g in grams)


def test_longest_first_dedup_keeps_one_variant():
    corpus = [(1, "Your wall pilates workout awaits."),
              (2, "Start a wall pilates workout now."),
              (3, "Best wall pilates workout ever.")]
    mined = mine_phrases(corpus, min_advertisers=2, limit=10)
    overlapping = [p for p in mined if "wall pilates" in p]
    assert len(overlapping) == 1
