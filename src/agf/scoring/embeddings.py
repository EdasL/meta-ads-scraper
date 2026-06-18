"""Content embeddings for the per-user preference ranker.

Pure helpers (build_embed_text / content_hash / l2_normalize) + the Gemini
network call (embed_texts). scripts/embed_apps.py does the DB I/O.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import urllib.request


def build_embed_text(*, app_name: str, tagline: str | None, genre: str | None,
                     description: str | None, ad_copy: list[str],
                     max_desc: int = 1200, max_ads: int = 8) -> str:
    """One string per app: name, tagline, category, truncated description, then
    the first max_ads DISTINCT ad-copy lines (order preserved)."""
    parts: list[str] = [app_name.strip()]
    if tagline:
        parts.append(tagline.strip())
    if genre:
        parts.append(genre.strip())
    if description:
        parts.append(description.strip()[:max_desc])
    seen: set[str] = set()
    kept = 0
    for line in ad_copy:
        line = (line or "").strip()
        if not line or line in seen:
            continue
        seen.add(line)
        parts.append(line)
        kept += 1
        if kept >= max_ads:
            break
    return "\n".join(p for p in parts if p)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm < 1e-12:
        return list(vec)
    return [x / norm for x in vec]


_EMBED_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
              "{model}:embedContent?key={key}")


class EmbedError(RuntimeError):
    pass


def _http_post(url: str, body: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except Exception as exc:  # noqa: BLE001 - surface any transport failure uniformly
        raise EmbedError(f"gemini embed POST failed: {exc!r}") from exc


def embed_texts(texts: list[str], *, api_key: str | None = None,
                model: str = "gemini-embedding-001", dim: int = 768,
                _post=None) -> list[list[float]]:
    """L2-normalized dim-length embedding per text. One request per text
    (embedContent is single-input); callers batch over apps and may sleep
    between calls. _post is injectable for tests."""
    key = api_key or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise EmbedError("no GEMINI_API_KEY")
    post = _post or _http_post
    url = _EMBED_URL.format(model=model, key=key)
    out: list[list[float]] = []
    for text in texts:
        resp = post(url, {"model": f"models/{model}",
                          "content": {"parts": [{"text": text}]},
                          "outputDimensionality": dim})
        values = resp.get("embedding", {}).get("values")
        if not values or len(values) != dim:
            raise EmbedError(f"expected {dim} dims, got {0 if not values else len(values)}")
        out.append(l2_normalize([float(x) for x in values]))
    return out
