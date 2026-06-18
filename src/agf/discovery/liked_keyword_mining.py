"""Taste-driven keyword miner: a cheap Gemini model proposes Meta-Ad-Library
search phrases that would find apps SIMILAR to the user's liked apps. Distinct
from keyword_mining.py (the TF-IDF flywheel over all scored gems). Proposed
phrases land as dynamic_keywords status='pending' until a human promotes them.
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field

_GEN_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
            "{model}:generateContent?key={key}")

_SCHEMA = {
    "type": "object",
    "properties": {"phrases": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "phrase": {"type": "string"},
            "rationale": {"type": "string"},
            "inspired_by": {"type": "array", "items": {"type": "integer"}},
        },
        "required": ["phrase", "rationale", "inspired_by"]}}},
    "required": ["phrases"],
}

_PROMPT = (
    "You pick Meta Ad Library search phrases that would surface NEW mobile apps "
    "similar to the ones a user liked. Propose up to {n} short phrases (2-4 words, "
    "lowercase, the kind of words these apps put in their ad copy or store listing). "
    "Avoid generic ad-speak (free trial, download now). For each phrase give a one-line "
    "rationale and the advertiser_id(s) it was inspired by.\n\nLiked apps:\n{apps}")


@dataclass
class Candidate:
    phrase: str
    rationale: str
    inspired_by: list[int] = field(default_factory=list)


class MiningError(RuntimeError):
    pass


def _http_post(url: str, body: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def dedup_candidates(candidates: list[Candidate], existing: set[str]) -> list[Candidate]:
    existing_lc = {e.lower() for e in existing}
    out, seen = [], set()
    for c in candidates:
        key = c.phrase.strip().lower()
        if not key or key in existing_lc or key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def _format_apps(liked_apps: list[dict]) -> str:
    lines = []
    for a in liked_apps:
        copy = " / ".join((a.get("ad_copy") or [])[:3])
        lines.append(f"- [{a['advertiser_id']}] {a.get('name','')}: "
                     f"{(a.get('description') or '')[:200]} || {copy}")
    return "\n".join(lines)


def propose_phrases(liked_apps: list[dict], *, api_key: str | None = None,
                    model: str = "gemini-2.0-flash-lite", n: int = 15,
                    _post=None) -> list[Candidate]:
    if not liked_apps:
        return []
    key = api_key or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise MiningError("no GEMINI_API_KEY")
    post = _post or _http_post
    body = {
        "contents": [{"parts": [{"text": _PROMPT.format(
            n=n, apps=_format_apps(liked_apps))}]}],
        "generationConfig": {"responseMimeType": "application/json",
                             "responseSchema": _SCHEMA},
    }
    resp = post(_GEN_URL.format(model=model, key=key), body)
    try:
        text = resp["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        raise MiningError(f"unparseable Gemini reply: {exc!r}") from exc
    out = []
    for p in parsed.get("phrases", []):
        phrase = (p.get("phrase") or "").strip()
        if phrase:
            out.append(Candidate(phrase, (p.get("rationale") or "").strip(),
                                 [int(x) for x in (p.get("inspired_by") or [])]))
    return out
