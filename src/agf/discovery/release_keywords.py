"""Taste-blind LLM keyword proposer. Given App Store new-release listings in one store
category, a cheap Gemini model proposes crisp Meta Ad Library SEARCH keywords ("X app"
niche/pain-point terms) that would find SIMILAR emerging apps.

Taste-blind: the prompt is about the CATEGORY and the example apps, never the user's
likes or the preference model. This replaces n-gram mining of store-description prose
(mine_phrases), which yields boilerplate ("key features") because store descriptions
share generic marketing text across apps — an LLM instead generalizes a few examples
into the crisp niche vocabulary the sweep actually wants.

See docs/superpowers/specs/2026-07-02-keyword-minting-frame-design.md (Post-implementation
finding)."""
import json
import os

from agf.net.http_backoff import HttpBackoffError, post_json

_GEN_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
            "{model}:generateContent?key={key}")
# gemini-2.0-flash-lite 404s on the current key; 2.5-flash-lite is the cheap working model.
_MODEL = "gemini-2.5-flash-lite"

_SCHEMA = {
    "type": "object",
    "properties": {"phrases": {"type": "array", "items": {"type": "string"}}},
    "required": ["phrases"],
}

_PROMPT = (
    "Propose up to {n} Meta Ad Library search keywords for finding NEW, emerging mobile "
    "apps in the '{category}' category, similar to the example apps below. Each keyword is "
    "a short niche or pain-point phrase (2-4 words, lowercase, the kind of term a person "
    "searches — usually ending in 'app', e.g. 'medication reminder app', 'ai hairstyle "
    "app', 'invoice maker app'). Rules: no brand or product names; no generic ad-speak "
    "(free trial, download now, key features, best app); no single words. Return only the "
    "phrases.\n\nExample new-release apps:\n{apps}")


class ProposeError(RuntimeError):
    pass


def _normalize(phrases) -> list[str]:
    """Lowercase, collapse whitespace, keep 2-5 word phrases, dedup (order-stable)."""
    out, seen = [], set()
    for p in phrases:
        norm = " ".join(str(p).lower().split())
        if norm and 1 < len(norm.split()) <= 5 and norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out


def propose_release_keywords(category: str, listings: list[dict], n: int, *,
                             api_key: str | None = None, model: str = _MODEL,
                             _post=None) -> list[str]:
    """Return up to `n` taste-blind niche search keywords for `category`, generalized from
    `listings` (dicts with 'name' + 'description'). Raises ProposeError on missing key or
    an unparseable/failed Gemini reply — callers isolate it as a per-category error."""
    if not listings or n <= 0:
        return []
    key = api_key or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise ProposeError("no GEMINI_API_KEY")
    apps = "\n".join(
        f"- {(l.get('name') or '').strip()}: {(l.get('description') or '')[:200]}"
        for l in listings[:10])
    body = {
        "contents": [{"parts": [{"text": _PROMPT.format(n=n, category=category, apps=apps)}]}],
        "generationConfig": {"responseMimeType": "application/json",
                             "responseSchema": _SCHEMA},
    }
    post = _post or (lambda url, b: post_json(url, b, max_retries=5, timeout=60))
    try:
        resp = post(_GEN_URL.format(model=model, key=key), body)
        text = resp["candidates"][0]["content"]["parts"][0]["text"]
        phrases = json.loads(text).get("phrases", [])
    except (HttpBackoffError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ProposeError(f"gemini propose failed: {exc!r}") from exc
    return _normalize(phrases)[:n]
