"""Keyword taxonomy for steering Meta Ad Library discovery toward emerging apps/funnels.

Probed insight (2026-06-10): a NARROW keyword returns small emerging advertisers even when
impression-sorted — the keyword does the filtering, not the sort. So discovery is a sweep of
well-chosen niche/intent keywords, each pulled shallow, then page_like_count-vetoed.

Three layers:
- FUNNEL_PHRASES   — quiz/funnel-intent exact phrases; the near-unique fingerprint of the
                     emerging web2app subscription cohort (lowest incumbent contamination).
                     Run as KEYWORD_EXACT_PHRASE.
- NICHE_TERMS      — app-category / pain-point terms. Run as KEYWORD_UNORDERED.
- STORE_FRAGMENTS  — app-store URL/CTA fragments; a broad app-install baseline catcher.

Extend freely — adding a keyword is a one-line change. The harness mines discovered ad copy
for new terms (flywheel) and prints them for promotion here.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class TaxonomyEntry:
    keyword: str
    layer: str          # "funnel_phrase" | "niche_term" | "store_fragment"
    search_type: str    # "KEYWORD_EXACT_PHRASE" | "KEYWORD_UNORDERED"
    max_pages: int


# Layer 1 — funnel / quiz-intent exact phrases (highest yield, lowest incumbent contamination)
FUNNEL_PHRASES = [
    "take the quiz", "personalized plan", "based on your answers", "your custom plan",
    "start your journey", "tailored just for you", "get your personalized", "answer a few questions",
    "your 28-day plan", "your 30-day plan", "your 21-day plan", "designed for your body",
    "find your perfect", "tell us about yourself", "see your results", "what's your goal",
    "get matched", "build your plan", "unlock your plan", "take a quick quiz",
    "your personalized plan", "made just for you", "discover your type", "tailored to you",
    "personalized program", "your wellness plan", "custom meal plan", "your reading plan",
]

# Layer 2 — app-niche / pain-point terms (non-game leaning)
NICHE_TERMS = [
    "calorie tracker app", "habit tracker app", "sleep sounds app", "meditation app",
    "anxiety relief app", "language learning app", "journaling app", "workout plan app",
    "intermittent fasting app", "cycle tracking app", "budgeting app", "sobriety app",
    "posture corrector app", "brain training app", "breathing exercise app", "water reminder app",
    "gratitude journal app", "focus timer app", "weight loss app", "skin care routine app",
    "plant identifier app", "AI tutor app", "manifestation app", "couples relationship app",
    "pelvic floor app", "menopause app", "pilates app", "running coach app",
]

# Layer 3 — store-URL / install-CTA fragments (broad app-install baseline)
STORE_FRAGMENTS = [
    "apps.apple.com", "play.google.com", "download on the app store",
    "get it on google play", "download the app", "available on ios", "available on android",
]


def _entries(words, layer, search_type, max_pages):
    return [TaxonomyEntry(w, layer, search_type, max_pages) for w in words]


def all_entries(layer: str | None = None) -> list[TaxonomyEntry]:
    """All taxonomy entries, optionally filtered to one layer."""
    entries = (
        _entries(FUNNEL_PHRASES, "funnel_phrase", "KEYWORD_EXACT_PHRASE", 3)
        + _entries(NICHE_TERMS, "niche_term", "KEYWORD_UNORDERED", 4)
        + _entries(STORE_FRAGMENTS, "store_fragment", "KEYWORD_UNORDERED", 3)
    )
    return [e for e in entries if layer is None or e.layer == layer]
