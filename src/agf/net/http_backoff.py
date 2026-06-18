"""Shared HTTP POST with exponential backoff for retriable status codes.

Extracted from agf.scoring.embeddings and agf.discovery.liked_keyword_mining —
both had identical retry/backoff loops. Callers wrap HttpBackoffError into their
own domain exception (EmbedError, MiningError) to preserve existing test contracts.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

_RETRIABLE_STATUS = frozenset({429, 500, 503})


class HttpBackoffError(RuntimeError):
    pass


def post_json(
    url: str,
    body: dict,
    *,
    max_retries: int = 6,
    timeout: int = 30,
    _open=None,
    _sleep=time.sleep,
) -> dict:
    """POST *body* as JSON to *url* with exponential backoff on 429/500/503.

    Honors Retry-After when present; otherwise backs off min(2**attempt, 60)
    seconds. *_open* and *_sleep* are injectable for tests.

    Raises HttpBackoffError on non-retriable HTTP errors, transport errors,
    or after exhausting *max_retries* attempts.
    """
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    opener = _open or (lambda r: urllib.request.urlopen(r, timeout=timeout))
    for attempt in range(max_retries + 1):
        try:
            with opener(req) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code in _RETRIABLE_STATUS and attempt < max_retries:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    delay = float(retry_after) if retry_after else min(2 ** attempt, 60)
                except (TypeError, ValueError):
                    delay = min(2 ** attempt, 60)
                _sleep(delay)
                continue
            raise HttpBackoffError(f"HTTP POST failed: {exc!r}") from exc
        except Exception as exc:  # noqa: BLE001
            raise HttpBackoffError(f"HTTP POST failed: {exc!r}") from exc
    raise HttpBackoffError("HTTP POST failed: retries exhausted")
