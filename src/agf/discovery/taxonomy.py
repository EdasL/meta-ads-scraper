"""Keyword taxonomy for steering Meta Ad Library discovery toward emerging apps/funnels.

Probed insight (2026-06-10): a NARROW keyword returns small emerging advertisers even when
impression-sorted — the keyword does the filtering, not the sort. So discovery is a sweep of
well-chosen niche/intent keywords, each pulled shallow. (The page_like_count sweep veto
was removed 2026-07-10; incumbents are filtered at triage/scoring instead.)

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

# Layer 2 — app-niche terms, balanced across the store category frame. Each
# in-scope category holds an equal quota (NICHE_PER_CATEGORY_MIN..MAX) of narrow,
# `app`-suffixed terms. Balance is asserted by tests/discovery/test_taxonomy_balance.py
# so the frame cannot silently re-skew. Source: bake-off-validated
# docs/research/keyword-experiments/full-seed-v1.txt (see that dir's RESULTS.md).
# Regenerate a category's list when it saturates — do not hand-pile one vertical.
NICHE_PER_CATEGORY_MIN = 8
NICHE_PER_CATEGORY_MAX = 12

# In-scope = every consumer store category; only Games (out of product scope,
# vetoed via is_game) and Stickers (not standalone apps) are excluded.
IN_SCOPE_CATEGORIES = frozenset({
    "Art & Design", "Auto & Vehicles", "Beauty", "Books", "Business", "Comics",
    "Communication", "Dating", "Education", "Entertainment", "Events", "Finance",
    "Food & Drink", "Health & Fitness", "House & Home", "Lifestyle", "Medical",
    "Music", "Navigation", "News", "Parenting", "Personalization", "Photo & Video",
    "Productivity", "Reference", "Shopping", "Social", "Sports", "Travel",
    "Utilities", "Video Players", "Weather",
})

NICHE_TERMS_BY_CATEGORY: dict[str, list[str]] = {
    'Art & Design': [
        'ai logo maker app', 'coloring book app', 'ai art generator app',
        'drawing tutorial app', 'calligraphy practice app', 'tattoo design app',
        'color palette app', 'vision board app', 'ai avatar maker app',
    ],
    'Auto & Vehicles': [
        'car maintenance app', 'obd2 scanner app', 'mileage tracker app', 'fuel log app',
        'car wash booking app', 'vehicle history app', 'parking reminder app',
        'ev charging map app', 'dash cam app',
    ],
    'Beauty': [
        'ai hairstyle app', 'skincare routine app', 'face yoga app', 'makeup tutorial app',
        'nail design app', 'skin analysis app', 'hair color try on app', 'glow up app',
        'perfume finder app',
    ],
    'Books': [
        'book summaries app', 'reading tracker app', 'ai reading coach app',
        'audiobook player app', 'ebook reader app', 'book club app', 'speed reading app',
        'quote of the day app', 'reading challenge app',
    ],
    'Business': [
        'ai resume builder app', 'invoice maker app', 'business card scanner app',
        'expense report app', 'time tracking app', 'freelance contract app', 'cold email app',
        'networking crm app', 'ai cover letter app',
    ],
    'Comics': [
        'comic reader app', 'webtoon app', 'manga reader app', 'ai comic maker app',
        'manhwa app', 'comic collection tracker app', 'graphic novel app',
        'comic strip maker app', 'anime news app',
    ],
    'Communication': [
        'ai voice translator app', 'private messenger app', 'walkie talkie app',
        'video call app', 'group chat app', 'voice changer app', 'anonymous chat app',
        'language exchange app', 'ai email writer app',
    ],
    'Dating': [
        'relationship quiz app', 'ai dating photos app', 'attachment style app',
        'couples connection app', 'flirting coach app', 'long distance couple app',
        'blind date app', 'dating profile review app', 'love language app',
    ],
    'Education': [
        'learn spanish app', 'math homework app', 'ai tutor app', 'flashcards app',
        'language learning app', 'study planner app', 'ielts prep app',
        'handwriting practice app', 'ai essay helper app',
    ],
    'Entertainment': [
        'ai companion app', 'short drama app', 'fan fiction app', 'trivia quiz app',
        'celebrity news app', 'karaoke app', 'movie tracker app', 'fanfic reader app',
        'roleplay chat app',
    ],
    'Events': [
        'wedding planning app', 'party countdown app', 'event guest list app',
        'birthday reminder app', 'rsvp tracker app', 'seating chart app',
        'festival schedule app', 'baby shower planner app', 'save the date app',
    ],
    'Finance': [
        'budgeting app', 'debt payoff app', 'expense tracker app', 'savings challenge app',
        'bill reminder app', 'net worth tracker app', 'subscription tracker app',
        'ai money coach app', 'paycheck planner app',
    ],
    'Food & Drink': [
        'macro tracker app', 'recipe organizer app', 'meal prep planner app',
        'calorie counter app', 'grocery list app', 'ai recipe app', 'intermittent fasting app',
        'wine pairing app', 'cocktail recipes app',
    ],
    'Health & Fitness': [
        'wall pilates app', 'workout planner app', 'step counter app', 'sleep tracker app',
        'water reminder app', 'menopause app', 'pelvic floor app', 'breathwork app',
        'mobility routine app',
    ],
    'House & Home': [
        'home renovation app', 'interior design app', 'home inventory app',
        'cleaning schedule app', 'plant care app', 'moving checklist app', 'room planner app',
        'ai decor app', 'home maintenance app',
    ],
    'Lifestyle': [
        'manifestation app', 'habit tracker app', 'gratitude journal app',
        'daily affirmations app', 'self care app', 'capsule wardrobe app', 'declutter app',
        'morning routine app', 'bucket list app',
    ],
    'Medical': [
        'blood pressure log app', 'migraine tracker app', 'symptom tracker app',
        'medication reminder app', 'period tracker app', 'glucose log app',
        'pill identifier app', 'doctor visit notes app', 'hearing test app',
    ],
    'Music': [
        'learn piano app', 'ai song maker app', 'guitar tuner app', 'ear training app',
        'metronome app', 'music theory app', 'beat maker app', 'vocal warmup app',
        'playlist maker app',
    ],
    'Navigation': [
        'road trip planner app', 'offline maps app', 'hiking trail app', 'rv trip app',
        'gps speedometer app', 'find my car app', 'transit map app', 'ev route planner app',
        'compass app',
    ],
    'News': [
        'local news app', 'ai news summary app', 'news digest app', 'breaking news app',
        'financial news app', 'fact check app', 'newsletter reader app', 'world news app',
        'tech news app',
    ],
    'Parenting': [
        'baby sleep app', 'potty training app', 'baby milestones app', 'contraction timer app',
        'breastfeeding tracker app', 'kids chore app', 'co parenting app', 'baby names app',
        'pregnancy tracker app',
    ],
    'Personalization': [
        'widget maker app', 'lock screen themes app', 'live wallpaper app', 'icon pack app',
        'keyboard themes app', 'ringtone maker app', 'home screen setup app',
        'custom font app', 'aesthetic widgets app',
    ],
    'Photo & Video': [
        'ai headshot app', 'photo restoration app', 'video editor app', 'photo collage app',
        'ai photo enhancer app', 'background remover app', 'slideshow maker app',
        'photo organizer app', 'ai profile photo app',
    ],
    'Productivity': [
        'ai meeting notes app', 'adhd planner app', 'focus timer app', 'daily planner app',
        'note taking app', 'voice memo app', 'task manager app', 'pomodoro app',
        'ai writing assistant app',
    ],
    'Reference': [
        'plant identifier app', 'bird identifier app', 'ai scanner app', 'dictionary app',
        'mushroom identifier app', 'rock identifier app', 'star map app', 'sign language app',
        'unit converter app',
    ],
    'Shopping': [
        'price tracker app', 'ai outfit app', 'coupon finder app', 'wishlist app',
        'receipt scanner app', 'deal alerts app', 'thrift finder app', 'barcode scanner app',
        'package tracker app',
    ],
    'Social': [
        'anonymous q and a app', 'private journal app', 'close friends app',
        'find friends app', 'community chat app', 'confession app', 'mood sharing app',
        'group planning app', 'social challenge app',
    ],
    'Sports': [
        'pickleball app', 'marathon training app', 'golf gps app', 'running coach app',
        'soccer scores app', 'fantasy league app', 'climbing log app', 'tennis tracker app',
        'cycling tracker app',
    ],
    'Travel': [
        'ai itinerary app', 'cheap flights app', 'packing list app', 'travel journal app',
        'currency converter app', 'trip budget app', 'layover guide app', 'road trip app',
        'visa requirement app',
    ],
    'Utilities': [
        'document scanner app', 'wifi analyzer app', 'file manager app', 'qr code scanner app',
        'battery saver app', 'phone cleaner app', 'screen mirror app', 'flashlight app',
        'noise meter app',
    ],
    'Video Players': [
        'video subtitle app', 'screen recorder app', 'video compressor app',
        'video downloader app', 'video to mp3 app', 'video speed app', 'loop video app',
        'ai video maker app', 'subtitle translator app',
    ],
    'Weather': [
        'rain alert app', 'hyperlocal weather app', 'aurora forecast app', 'storm tracker app',
        'pollen count app', 'uv index app', 'marine weather app', 'snow forecast app',
        'weather widget app',
    ],
}

NICHE_TERMS = [t for terms in NICHE_TERMS_BY_CATEGORY.values() for t in terms]

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
