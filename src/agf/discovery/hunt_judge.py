"""LLM relevance judge for hunt rounds.

Ad Library keyword search returns pages that match the term but not the
founder's ask. After triage keeps a result, this module judges it against the
round's intent (one founder sentence) using Gemini structured output.

Fail-open by design: any error leaves relevance NULL, which every reader
treats as relevant. A judge outage must never hide results.

Network calls run OUTSIDE any DB transaction; reads and writes are their own
short transactions.
"""
from __future__ import annotations

import json
import logging
import os

from sqlalchemy import text

from agf.db.engine import begin
from agf.net.http_backoff import HttpBackoffError, post_json

log = logging.getLogger(__name__)

_GEN_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
            "{model}:generateContent?key={key}")
_MODEL = "gemini-2.5-flash-lite"
_BATCH = 20

_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "relevant": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "relevant", "reason"],
            },
        }
    },
    "required": ["verdicts"],
}

_PROMPT = (
    "The founder is hunting: {intent}\n\n"
    "Each numbered item below is an app found by keyword search in an ad "
    "library. Decide for each whether it plausibly matches what the founder "
    "is hunting. Keyword overlap alone is not relevance; judge from the name, "
    "genre, store listing, and ad text. reason is one short plain sentence.\n\n"
    "{items}"
)


def _kept_rows(conn, round_id: int):
    return conn.execute(text(
        "SELECT hr.id, hr.page_name, a.name AS app_name, a.genre, a.description, ct.body_text "
        "FROM frontend.hunt_results hr "
        "LEFT JOIN public.apps a ON a.store = hr.store AND a.store_id = hr.store_id "
        "LEFT JOIN LATERAL ("
        "    SELECT c.body_text FROM public.ads ad "
        "    JOIN public.creatives c ON c.ad_id = ad.id "
        "    WHERE ad.advertiser_id = hr.kept_advertiser_id AND c.body_text IS NOT NULL "
        "    LIMIT 1"
        ") ct ON true "
        "WHERE hr.round_id = :r AND hr.triage = 'kept'"), {"r": round_id}).fetchall()


def _item_line(r) -> str:
    listing = (r.description or "")[:300]
    ad = (r.body_text or "")[:200]
    return (f"id {r.id}: {r.page_name or r.app_name or '?'}"
            f" | genre: {r.genre or '?'}"
            f" | listing: {listing or '?'}"
            f" | ad text: {ad or '?'}")


def judge_round(engine, round_id: int, intent, *, model: str = _MODEL,
                api_key: str | None = None, _post=None) -> dict:
    """Judge a round's kept results against its intent. Returns counts."""
    intent = (intent or "").strip()
    if not intent:
        return {"judged": 0, "off_target": 0, "skipped": "no intent"}
    key = api_key or os.environ.get("GEMINI_API_KEY")
    if not key:
        return {"judged": 0, "off_target": 0, "skipped": "no GEMINI_API_KEY"}

    with begin(engine) as conn:
        rows = _kept_rows(conn, round_id)
    if not rows:
        return {"judged": 0, "off_target": 0}

    post = _post or (lambda url, b: post_json(url, b, max_retries=3, timeout=60))
    verdicts: list[tuple[int, str, str]] = []
    for i in range(0, len(rows), _BATCH):
        batch = rows[i:i + _BATCH]
        body = {
            "contents": [{"parts": [{"text": _PROMPT.format(
                intent=intent, items="\n".join(_item_line(r) for r in batch))}]}],
            "generationConfig": {"responseMimeType": "application/json",
                                 "responseSchema": _SCHEMA},
        }
        try:
            resp = post(_GEN_URL.format(model=model, key=key), body)
            parsed = json.loads(resp["candidates"][0]["content"]["parts"][0]["text"])
            batch_verdicts = parsed["verdicts"]
        except (HttpBackoffError, KeyError, IndexError, TypeError, ValueError) as exc:
            log.warning("hunt judge batch failed (fail open): %s", exc)
            continue
        # Fail open: valid JSON can still violate the schema (e.g. verdicts
        # null or a scalar). Guard the shape so a bad batch is skipped, never
        # crashes the round.
        if not isinstance(batch_verdicts, list):
            log.warning("hunt judge batch verdicts not a list (fail open): %r", type(batch_verdicts))
            continue
        ids = {r.id for r in batch}
        for v in batch_verdicts:
            if not isinstance(v, dict) or v.get("id") not in ids:
                continue
            rel = "relevant" if v.get("relevant") else "off_target"
            verdicts.append((int(v["id"]), rel, str(v.get("reason") or "")[:280]))

    off = 0
    if verdicts:
        with begin(engine) as conn:
            for rid, rel, note in verdicts:
                conn.execute(text(
                    "UPDATE frontend.hunt_results "
                    "SET relevance = :rel, relevance_note = :note WHERE id = :i"),
                    {"rel": rel, "note": note, "i": rid})
                off += rel == "off_target"
    return {"judged": len(verdicts), "off_target": off}
