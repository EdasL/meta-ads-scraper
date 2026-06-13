"""The keyword flywheel's miner (pure): recurring phrases from the ad copy of scored
gems become trial sweep keywords. The cohort signal is CROSS-ADVERTISER recurrence —
a phrase counts once per advertiser, so one brand's repeated slogan never qualifies,
but language shared by several validated gems ("streak tracking", "ai coach") does.

fetch_gem_copy is the only DB touch (read-only); mine_phrases is pure so the ranking
and filters are unit-testable. Promotion/retirement of mined keywords lives in
db/dynamic_keywords.py; the worker stage wires the two together."""
import re
from collections import defaultdict

# Function words a useful niche phrase never starts or ends with.
STOPWORDS = frozenset("""
a an and are as at be been by can could did do does for from get got had has have
how i if in is it its just me my no not now of on or our so than that the their
them then there these they this to too up us was we were what when which who why
will with would you your yours
""".split())

# Generic ad-speak that recurs across EVERY cohort — sweeping it finds noise, not a niche.
GENERIC_PHRASES = frozenset({
    "learn more", "sign up", "free trial", "download now", "get started",
    "limited time", "shop now", "try it free", "no credit card", "cancel anytime",
    "app store", "google play", "link in bio", "money back", "money back guarantee",
    "try for free", "download the app", "available now", "join thousands",
    "start today", "start now", "right now", "every day", "first week",
    "new app", "best app", "free app", "mobile app", "iphone app",
})

# Words too common in ANY ad copy to carry a niche on their own — a phrase must
# contain at least one substantial word outside this set (and outside STOPWORDS).
# "streak tracking" passes on "streak"; "minutes a day" / "track your progress" don't.
WEAK_WORDS = frozenset("""
day days week weeks month months year years minute minutes hour hours second seconds
time times today tonight tomorrow daily people person place places thing things
something anything everything everyone anyone life lives way ways works working
feel feels feeling felt like love loved start started starts starting stop stops
stopped guessing track tracks tracking tracked progress goal goals plan plans
free easy simple fast quick quickly real really first last next new old best
better good great finally actually need needs needed want wants wanted help helps
make makes made take takes left right results result loud
one two three four five ten hundred thousand million single every sure code
goodbye miss looks talks access trial pocket store google apple play download
app apps phone
""".split())

# High-frequency non-English function words — one of these in a phrase means the ad
# isn't English copy and the phrase isn't a sweepable US keyword.
FOREIGN_WORDS = frozenset("""
et votre vous avec pour les des une sur est dans pas plus para como con los las
del esta este ahora gratis descarga aplica mais und der die das nicht ist mit
sie auf voce nao seu sua
""".split())

_ACCENTED = re.compile(r"[\u00c0-\u024f]")
_TEMPLATE = re.compile(r"\{\{[^}]*\}\}")
_URL = re.compile(r"https?://\S+|www\.\S+")
_NON_ASCII = re.compile(r"[^\x00-\x7f]+")
_SENTENCE_SPLIT = re.compile(r"[.!?;:\n\r•|()\[\]\"]+")
_WORD = re.compile(r"[a-z0-9][a-z0-9'-]*")


def _sentences(text: str) -> list[list[str]]:
    """Normalized token lists, one per sentence-ish chunk (n-grams never cross them)."""
    text = (text or "").replace("\u2019", "'")        # typographic apostrophe
    text = _TEMPLATE.sub(" ", text)
    text = _URL.sub(" ", text)
    text = _NON_ASCII.sub(" ", text).lower()
    return [toks for chunk in _SENTENCE_SPLIT.split(text)
            if (toks := _WORD.findall(chunk))]


def _phrase_ok(tokens: tuple[str, ...]) -> bool:
    if tokens[0] in STOPWORDS or tokens[-1] in STOPWORDS:
        return False
    # contractions ("didn't", "here's") and bare numbers ("7 days") never carry a niche
    if any("'" in t or t.isdigit() or t in FOREIGN_WORDS for t in tokens):
        return False
    phrase = " ".join(tokens)
    if phrase in GENERIC_PHRASES:
        return False
    # at least one substantial content word — drops "of the" / "minutes a day" shapes
    return any(len(t) >= 4 and t not in STOPWORDS and t not in WEAK_WORDS
               for t in tokens)


def phrases_of(text: str, *, n_min: int = 2, n_max: int = 4) -> set[str]:
    """All candidate n-gram phrases of one ad body (deduped). Accented letters mean
    non-English copy — stripping them mid-word would mint mojibake keywords, so the
    whole body is skipped instead."""
    if _ACCENTED.search(text or ""):
        return set()
    out = set()
    for toks in _sentences(text):
        for n in range(n_min, n_max + 1):
            for i in range(len(toks) - n + 1):
                gram = tuple(toks[i:i + n])
                if _phrase_ok(gram):
                    out.add(" ".join(gram))
    return out


def mine_phrases(corpus, *, exclude: set[str] = frozenset(),
                 min_advertisers: int = 2, limit: int = 10) -> list[str]:
    """Rank phrases by how many DISTINCT advertisers use them. `corpus` is an iterable
    of (advertiser_id, body_text). `exclude` is every keyword already known — the
    static taxonomy plus every dynamic keyword ever tried (retired ones stay dead) —
    matched both ways on containment so "habit tracker" won't re-mint when
    "habit tracker app" is already swept."""
    by_phrase: dict[str, set] = defaultdict(set)
    token_advs: dict[str, set] = defaultdict(set)
    all_advertisers: set = set()
    for advertiser_id, body in corpus:
        all_advertisers.add(advertiser_id)
        for phrase in phrases_of(body):
            by_phrase[phrase].add(advertiser_id)
            for tok in phrase.split():
                token_advs[tok].add(advertiser_id)
    exclude_lc = {e.lower() for e in exclude}
    total = len(all_advertisers)
    # niche language is shared by a FEW gems; copy shared by a large slice of the
    # whole corpus ("one place", "feel like") is universal ad-speak, not a niche.
    max_advertisers = max(5, round(0.1 * total))
    # ...and its carrying content word must itself be RARE in the corpus. This is the
    # structural filter that keeps "wall pilates" (pilates: a few gems) and drops
    # "know exactly" / "every time" (every token used across the whole corpus) —
    # self-calibrating, no blocklist to maintain.
    rare_max = max(5, round(0.05 * total))

    def carried_by_rare_word(phrase: str) -> bool:
        return any(len(t) >= 4 and t not in STOPWORDS and t not in WEAK_WORDS
                   and not any(ch.isdigit() for ch in t)      # "7-day" can't carry
                   and len(token_advs[t]) <= rare_max for t in phrase.split())

    def known(phrase: str) -> bool:
        return any(phrase in e or e in phrase for e in exclude_lc)

    ranked = sorted(
        ((phrase, advs) for phrase, advs in by_phrase.items()
         if min_advertisers <= len(advs) <= max_advertisers
         and carried_by_rare_word(phrase) and not known(phrase)),
        key=lambda kv: (-len(kv[1]), -len(kv[0].split()), kv[0]))
    # longest-first dedup: once "wall pilates workout" is in, drop its sub/super-phrases
    out: list[str] = []
    for phrase, _ in ranked:
        if any(phrase in p or p in phrase for p in out):
            continue
        out.append(phrase)
        if len(out) >= limit:
            break
    return out


def fetch_gem_copy(conn, *, min_score: float = 40.0):
    """(advertiser_id, body_text) rows for every scored gem with stored ad copy —
    the flywheel's mining corpus. Read-only; creatives are deduped exactly (the same
    body re-stored across an advertiser's ads counts once via mine_phrases anyway)."""
    from sqlalchemy import text
    return conn.execute(text(
        "SELECT DISTINCT m.advertiser_id, c.body_text "
        "FROM frontend.emerging_scaling_apps m "
        "JOIN public.ads a ON a.advertiser_id = m.advertiser_id "
        "JOIN public.creatives c ON c.ad_id = a.id "
        "WHERE m.gem_score >= :s AND NULLIF(c.body_text, '') IS NOT NULL"),
        {"s": min_score}).all()
