"""Content embeddings for the per-user preference ranker.

Pure helpers (build_embed_text / content_hash / l2_normalize) + the Gemini
network call (embed_texts). scripts/embed_apps.py does the DB I/O.
"""
from __future__ import annotations

import hashlib
import math


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
