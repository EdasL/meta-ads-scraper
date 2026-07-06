"""External keyword vocabulary from app-store search autocomplete (Apple hints + Google
Play suggest). Uncorrelated with our found-ad corpus by construction — this is Pipe 2's
breadth source. Returns normalized 'X app' style phrases. All fetches degrade to [] on
failure (per-seed isolation). See the 2026-07-06 novelty-engine spec."""
import plistlib

from agf.discovery.release_keywords import _normalize
from agf.discovery.taxonomy import IN_SCOPE_CATEGORIES, NICHE_TERMS_BY_CATEGORY
from agf.enrich_full.appstore import gplay_suggest_batch

_APPLE_HINTS = ("https://search.itunes.apple.com/WebObjects/MZSearchHints.woa/wa/hints"
                "?clientApplication=Software&term={term}")
_APPLE_HEADERS = {"X-Apple-Store-Front": "143441-1,29", "User-Agent": "iTunes/12.11"}


def apple_hints(client, term: str) -> list[str]:
    """Apple App Store search autocomplete terms for `term`. [] on any failure."""
    from urllib.parse import quote
    try:
        resp = client.get(_APPLE_HINTS.format(term=quote(term)), headers=_APPLE_HEADERS)
        data = plistlib.loads(resp.content)
        return [h["term"] for h in data.get("hints", []) if h.get("term")]
    except Exception:
        return []


def _seeds_for(category: str, n: int) -> list[str]:
    """Seed terms to expand for a category: the category name + its top taxonomy terms."""
    terms = [str(t) for t in NICHE_TERMS_BY_CATEGORY.get(category, [])][:n]
    return [category.lower(), *terms]


def gather_autocomplete_candidates(client, *, categories, seeds_per_category=3,
                                   apple=apple_hints,
                                   gplay_batch=gplay_suggest_batch) -> dict:
    """For each in-scope category, expand its seed terms through Apple + Google Play
    autocomplete, normalize to 'X app' phrases (dedup included), keyed per category.
    Google Play runs as ONE batched sidecar call for all seeds (a per-seed call spawns
    ~120 node processes per run). Each source failure degrades to []/{} (isolated).
    Zero Meta traffic."""
    seeds_by_cat = {cat: _seeds_for(cat, seeds_per_category)
                    for cat in categories if cat in IN_SCOPE_CATEGORIES}
    all_seeds, seen_seeds = [], set()
    for seeds in seeds_by_cat.values():
        for s in seeds:
            if s not in seen_seeds:
                seen_seeds.add(s)
                all_seeds.append(s)
    gp = gplay_batch(all_seeds) if all_seeds else {}
    out: dict[str, list[str]] = {}
    for cat, seeds in seeds_by_cat.items():
        raw: list[str] = []
        for seed in seeds:
            raw.extend(apple(client, seed))
            raw.extend(gp.get(seed, []))
        phrases = _normalize(raw)
        if phrases:
            out[cat] = phrases
    return out
