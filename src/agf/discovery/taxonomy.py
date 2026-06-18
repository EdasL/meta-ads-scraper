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

The daily production sweep uses `sweep_entries()` (niche+store only — funnel phrases find
funnels, which are v2).

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

# Layer 2 — app-niche / pain-point terms (non-game leaning). Grouped by vertical so
# additions stay reviewable; the probes showed NARROW keywords surface small apps.
_NICHE_CORE = [
    "calorie tracker app", "habit tracker app", "sleep sounds app", "meditation app",
    "anxiety relief app", "language learning app", "journaling app", "workout plan app",
    "intermittent fasting app", "cycle tracking app", "budgeting app", "sobriety app",
    "posture corrector app", "brain training app", "breathing exercise app", "water reminder app",
    "gratitude journal app", "focus timer app", "weight loss app", "skin care routine app",
    "plant identifier app", "AI tutor app", "manifestation app", "couples relationship app",
    "pelvic floor app", "menopause app", "pilates app", "running coach app",
]
_NICHE_FITNESS = [
    "wall pilates app", "kettlebell workout app", "calisthenics app", "chair yoga app",
    "stretching routine app", "mobility routine app", "somatic exercises app",
    "home workout no equipment app", "30 day fitness challenge app", "couch to 5k app",
    "marathon training app", "trail running app", "jump rope workout app",
    "boxing workout app", "resistance band workout app", "glute workout app",
    "abs workout app", "posture exercises app", "back pain relief app",
    "knee pain exercises app", "physical therapy exercises app", "foam rolling app",
    "walking for weight loss app", "step challenge app", "rucking app",
    "zone 2 training app", "heart rate training app", "swim workout app",
    "rowing workout app", "climbing training app", "golf swing analyzer app",
    "tennis training app", "pickleball app", "balance exercises seniors app",
    "workout for seniors app", "cold plunge app", "breathwork app",
]
_NICHE_HEALTH_WOMEN = [
    "perimenopause app", "hormone balance app", "pcos app", "fertility tracker app",
    "pregnancy workout app", "postpartum recovery app", "breastfeeding tracker app",
    "baby sleep app", "baby feeding tracker app", "contraction timer app",
    "face yoga app", "hair growth app", "skincare routine builder app", "skin analyzer app",
]
_NICHE_DIET = [
    "macro tracker app", "carnivore diet app", "keto diet app", "mediterranean diet app",
    "anti inflammatory diet app", "gluten free scanner app", "food intolerance app",
    "meal prep planner app", "grocery list app", "recipe organizer app", "ai recipe app",
    "calorie photo scanner app", "glucose tracker app", "gut health app",
    "protein tracker app", "supplement tracker app", "fasting tracker app",
    "weight loss for women app", "emotional eating app", "binge eating recovery app",
    "mindful eating app", "fiber tracker app",
]
_NICHE_SLEEP = [
    "sleep tracker app", "snore recording app", "white noise baby app", "brown noise app",
    "sleep stories app", "lucid dreaming app", "dream journal app", "smart alarm app",
    "insomnia therapy app", "shift work sleep app",
]
_NICHE_MENTAL = [
    "cbt therapy app", "dbt skills app", "adhd planner app", "adhd focus app",
    "ocd app", "panic attack relief app", "mood tracker app", "self esteem app",
    "shadow work app", "inner child healing app", "grief support app",
    "social anxiety app", "overthinking app", "ai therapist app", "affirmations app",
    "hypnosis app", "eft tapping app", "vagus nerve exercises app",
    "nervous system regulation app", "breathing for anxiety app", "stress relief games app",
    "quit smoking app", "quit vaping app", "quit drinking app", "nofap app",
    "porn addiction recovery app", "adhd body doubling app",
]
_NICHE_PRODUCTIVITY = [
    "habit streak app", "dopamine detox app", "screen time blocker app",
    "app blocker for focus", "phone addiction app", "digital detox app",
    "pomodoro timer app", "deep work app", "adhd task manager app", "daily planner app",
    "second brain app", "ai notes app", "voice notes transcription app",
    "meeting notes ai app", "goal tracker app", "morning routine app", "5am club app",
    "journaling prompts app", "gratitude practice app", "stoic journal app",
    "bullet journal app", "accountability partner app", "cleaning schedule app",
    "declutter app", "home inventory app",
]
_NICHE_LEARNING = [
    "learn spanish app", "learn japanese app", "learn korean app", "learn french app",
    "language immersion app", "vocabulary builder app", "ielts prep app", "sat prep app",
    "mcat prep app", "nclex prep app", "spaced repetition app", "speed reading app",
    "book summaries app", "reading tracker app", "learn to code app", "learn python app",
    "chess training app", "music theory app", "learn piano app", "learn guitar app",
    "ear training app", "drawing lessons app", "learn to sing app", "public speaking app",
    "memory training app", "trivia learning app", "history learning app", "philosophy app",
    "homework scanner app", "ai math solver app", "kids learning games app",
    "toddler learning app", "kids bedtime stories app", "brain games for seniors app",
]
_NICHE_AI_TOOLS = [
    "ai headshot app", "ai photo enhancer app", "ai art generator app",
    "ai video editor app", "ai voice changer app", "ai companion app",
    "ai resume builder app", "ai interview prep app", "ai essay writer app",
    "ai language tutor app", "ai fitness coach app", "ai nutrition coach app",
    "ai stylist app", "ai interior design app", "ai tattoo generator app",
    "ai baby face generator app", "ai dream interpreter app", "ai girlfriend app",
]
_NICHE_FINANCE = [
    "budget planner app", "zero based budget app", "envelope budgeting app",
    "couples expense tracker app", "debt payoff app", "credit score builder app",
    "save money challenge app", "round up savings app", "investing for beginners app",
    "paper trading app", "dividend tracker app", "crypto portfolio app",
    "net worth tracker app", "bill reminder app", "subscription tracker app",
    "side hustle app", "freelance invoice app", "receipt scanner app",
]
_NICHE_FAMILY_RELATION = [
    "co parenting app", "family organizer app", "kids chore chart app",
    "potty training app", "teen allowance app", "baby milestones app",
    "due date countdown app", "couples quiz app", "long distance relationship app",
    "date night ideas app", "marriage counseling app", "love language app",
    "attachment style app", "breakup recovery app", "conversation starters app",
]
_NICHE_SPIRITUAL = [
    "bible study app", "daily devotional app", "prayer journal app",
    "christian meditation app", "quran memorization app", "islamic prayer times app",
    "catholic rosary app", "tarot reading app", "astrology birth chart app",
    "human design app", "numerology app", "moon phases app",
    "manifestation journal app", "vision board app",
]
_NICHE_LIFESTYLE = [
    "plant care reminder app", "mushroom identifier app", "bird identifier app",
    "rock identifier app", "coin identifier app", "antique appraisal app",
    "wine scanner app", "outfit planner app", "capsule wardrobe app",
    "car maintenance app", "fuel tracker app", "road trip planner app",
    "travel itinerary app", "packing list app", "flight deals app",
    "fishing forecast app", "golf gps app", "disc golf app",
    "knitting counter app", "crochet patterns app", "woodworking plans app",
    "guitar tuner app", "karaoke app", "audiobook player app",
    "widget maker app", "watch faces app", "live wallpaper app",
    "hearing test app", "eye exercises app", "interior design planner app",
]
NICHE_TERMS = (_NICHE_CORE + _NICHE_FITNESS + _NICHE_HEALTH_WOMEN + _NICHE_DIET
               + _NICHE_SLEEP + _NICHE_MENTAL + _NICHE_PRODUCTIVITY + _NICHE_LEARNING
               + _NICHE_AI_TOOLS + _NICHE_FINANCE + _NICHE_FAMILY_RELATION
               + _NICHE_SPIRITUAL + _NICHE_LIFESTYLE)

# Layer 3 — store-URL / install-CTA fragments (broad app-install baseline)
STORE_FRAGMENTS = [
    "apps.apple.com", "play.google.com", "download on the app store",
    "get it on google play", "download the app", "available on ios", "available on android",
    "download our app", "install the app", "try the app free", "now on the app store",
    "download for free", "our new app",
]


def _entries(words, layer, search_type, max_pages):
    return [TaxonomyEntry(w, layer, search_type, max_pages) for w in words]


def all_entries(layer: str | None = None) -> list[TaxonomyEntry]:
    """All taxonomy entries, optionally filtered to one layer."""
    entries = (
        _entries(FUNNEL_PHRASES, "funnel_phrase", "KEYWORD_EXACT_PHRASE", 8)
        + _entries(NICHE_TERMS, "niche_term", "KEYWORD_UNORDERED", 4)
        + _entries(STORE_FRAGMENTS, "store_fragment", "KEYWORD_UNORDERED", 3)
    )
    return [e for e in entries if layer is None or e.layer == layer]


def sweep_entries() -> list[TaxonomyEntry]:
    """The daily production sweep: app-focused layers only. Funnel phrases find
    funnels (v2) — their proxy budget goes to app keywords instead."""
    return all_entries(layer="niche_term") + all_entries(layer="store_fragment")
