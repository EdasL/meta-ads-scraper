# Extraction Core Implementation Plan (Plan 2 of 7)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a durable, **browserless** Meta Ad Library scraper that warms a session once (solving Facebook's `rd_challenge` in pure HTTP), reuses it across many `AdLibrarySearchPaginationQuery` GraphQL POSTs through residential proxies, self-heals rotating tokens/`doc_id`, and exposes the frozen `Extractor` facade (master-plan §3.4) that Plans 4/5/6 consume.

**Architecture:** `curl_cffi` (Chrome-TLS impersonation) is the transport — plain `httpx` is blocked by Meta's TLS fingerprinting *and* the `rd_challenge` gate. Pure functions (error classification, proxy rotation, token/`doc_id` regex heal, `rd_challenge` path extraction, request building, response parsing, deeplink classification) are unit-tested against saved fixtures and a duck-typed **fake session**; the live `curl_cffi` session, the warm/pool lifecycle, and the optional browser warmer are thin I/O wrappers covered by opt-in `@integration` smoke tests. This empirically-proven approach (see `../research/2026-06-04-server-side-extraction.md`) replaces the original Playwright-interception design; Playwright/Camoufox remains only as a standby warmer fallback.

**Tech Stack:** Python 3.13, `curl_cffi` (transport + TLS), `pytest`. Reuses Plan 1 modules unchanged: `agf.fbjson.parse_fb_json`, `agf.bootstrap.extract_lsd`/`extract_doc_id`, `agf.request.build_variables`, `agf.parse.parse_search_response`+`AdRecord`, `agf.classify.classify_url`+`AppIntent`. Standard library `re`, `time`, `random`, `os`, `itertools`, `dataclasses`, `asyncio`. Playwright is an **optional** standby dependency (Task 11), never required for the hot path.

**Companion docs:** Master plan `00-master-plan.md` (§3.4 `Extractor` contract is FROZEN — build against it). Research/rationale + on-machine proof: `../research/2026-06-04-server-side-extraction.md`.

---

## Coordination contract reminders (from master-plan §3 — do not violate)

- **§3.4 `Extractor` facade is frozen.** Final signatures (async):
  ```python
  class Extractor:                       # src/agf/extract/facade.py
      async def search(self, *, query='', countries=['US'], active_status='ACTIVE',
                       sort_by_impressions=False, search_type='KEYWORD_UNORDERED',
                       view_all_page_id=None, max_pages=10) -> list[AdRecord]
      async def ad_details(self, ad_archive_id) -> AdDetails
      async def resolve_deeplink(self, url, ua='ios') -> ResolvedLink
  ```
- **§3.10 proxy env:** single contract `AGF_PROXIES` (newline/comma list of `user:pass@host:port`), read by `ProxyPool.from_env`.
- **requirements.txt** is shared, append-only, one labelled section per phase. This plan appends a `# --- plan-2 ---` section only.
- **Never redeclare Plan 1 dataclasses/functions** — import them.

---

## File structure (this plan)

```
emerging-app-finder/
  requirements.txt                      # append "# --- plan-2 ---" : curl_cffi (+ playwright optional)
  src/agf/extract/
    __init__.py
    errors.py        # ExtractError + error taxonomy enum
    backoff.py       # classify_error() + with_backoff() jittered exponential retry
    proxy.py         # ProxyPool.from_env (round-robin, dead-proxy cooldown), as_curl_proxies()
    rd_challenge.py  # extract_challenge_path() + solve_rd_challenge(session)
    token_heal.py    # SessionTokens + extract_lsd/extract_fb_dtsg/heal_doc_id/heal_from_html
    tls_client.py    # AdLibrarySession (curl_cffi Session wrapper) + build_session_headers
    search.py        # build_search_variables() + search_ads() paginated
    ad_details.py    # AdDetails + fetch_ad_details() (eu_total_reach, active)
    deeplink.py      # ResolvedLink + resolve_deeplink() (iOS-UA redirect -> classify)
    session_pool.py  # WarmSession + SessionPool (warm-once, staleness, re-warm)
    warmer.py        # OPTIONAL Playwright/Camoufox standby warmer (integration-only)
    facade.py        # Extractor (the §3.4 facade) wrapping SessionPool
  tests/extract/
    conftest.py                         # FakeSession + sample-token fixtures
    fixtures/
      rd_challenge_stub.html            # the ~481-byte 403 body
      ads_library_real.html             # trimmed real page HTML (LSD + bundle doc_id)
      search_page1.json  search_page2.json
      ad_details.json
    test_backoff.py test_proxy.py test_rd_challenge.py test_token_heal.py
    test_tls_client.py test_search.py test_ad_details.py test_deeplink.py
    test_session_pool.py test_facade.py
```

**Module responsibilities are one-each and pure** except `tls_client.py`, `session_pool.py`, `warmer.py` (the I/O modules). Unit tests use a **`FakeSession`** (duck-typed: `get(url)`/`post(url, data=, headers=)` returning a `FakeResponse(status_code, text, cookies)`); network is covered by opt-in `@integration` smoke tests.

---

### Task 1: Scaffold `extract/`, append deps, shared test fixtures

**Files:**
- Create: `src/agf/extract/__init__.py`
- Create: `src/agf/extract/errors.py`
- Modify: `requirements.txt` (append plan-2 section)
- Create: `tests/extract/__init__.py`
- Create: `tests/extract/conftest.py`

- [ ] **Step 1: Append the plan-2 dependency section**

Append to `requirements.txt` (do NOT edit other sections):
```
# --- plan-2 ---
curl_cffi==0.15.0
# playwright is an OPTIONAL standby warmer (Task 11); install only if used:
#   playwright==1.49.0   (+ `playwright install chromium`)
```

- [ ] **Step 2: Create the package marker + error taxonomy**

`src/agf/extract/__init__.py`:
```python
"""Emerging App Finder — Plan 2 extraction core (curl_cffi + rd_challenge)."""
```

`src/agf/extract/errors.py`:
```python
from enum import Enum


class ErrorKind(str, Enum):
    RATE_LIMIT = "rate_limit"      # 429, FB code 1675004
    SESSION = "session"            # FB codes 1357004 / 1357001, 403 + rd_challenge
    TRANSIENT = "transient"        # FB code 368, 5xx
    FATAL = "fatal"                # anything else


class ExtractError(Exception):
    """Raised when extraction fails after retries; carries the classified kind."""

    def __init__(self, message: str, kind: ErrorKind = ErrorKind.FATAL):
        super().__init__(message)
        self.kind = kind
```

- [ ] **Step 3: Create the shared fake session + token fixtures**

`tests/extract/__init__.py`:
```python
```

`tests/extract/conftest.py`:
```python
from dataclasses import dataclass, field
import pytest


@dataclass
class FakeResponse:
    status_code: int = 200
    text: str = ""
    _json: dict | None = None

    def json(self) -> dict:
        if self._json is None:
            raise ValueError("no json")
        return self._json


@dataclass
class FakeSession:
    """Duck-typed stand-in for AdLibrarySession / curl_cffi Session.

    Queue responses per (method, url-substring); records calls for assertions.
    """
    responses: dict = field(default_factory=dict)   # (method, substr) -> list[FakeResponse]
    cookies: dict = field(default_factory=dict)
    calls: list = field(default_factory=list)

    def _pop(self, method: str, url: str) -> FakeResponse:
        self.calls.append((method, url))
        for (m, substr), queue in self.responses.items():
            if m == method and substr in url and queue:
                return queue.pop(0)
        return FakeResponse(status_code=200, text="")

    def get(self, url, **kw):
        return self._pop("GET", url)

    def post(self, url, data=None, headers=None, **kw):
        return self._pop("POST", url)


@pytest.fixture
def fake_session():
    return FakeSession()
```

- [ ] **Step 4: Verify the package imports**

Run: `cd emerging-app-finder && .venv/bin/python -c "import sys; sys.path.insert(0,'src'); import agf.extract, agf.extract.errors"`
Expected: no output, exit 0.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt src/agf/extract/__init__.py src/agf/extract/errors.py tests/extract/__init__.py tests/extract/conftest.py
git commit -m "chore(plan-2): scaffold extract package + curl_cffi dep + fake session fixtures"
```

---

### Task 2: `backoff.py` — error classification + jittered retry

**Files:**
- Create: `src/agf/extract/backoff.py`
- Test: `tests/extract/test_backoff.py`

- [ ] **Step 1: Write the failing test**

`tests/extract/test_backoff.py`:
```python
import pytest
from agf.extract.backoff import classify_error, with_backoff
from agf.extract.errors import ErrorKind, ExtractError


def test_classify_http_status():
    assert classify_error(status=429) is ErrorKind.RATE_LIMIT
    assert classify_error(status=503) is ErrorKind.TRANSIENT
    assert classify_error(status=403) is ErrorKind.SESSION
    assert classify_error(status=200) is ErrorKind.FATAL  # 200 but called => unknown


def test_classify_fb_error_codes():
    assert classify_error(fb_code=1675004) is ErrorKind.RATE_LIMIT
    assert classify_error(fb_code=1357004) is ErrorKind.SESSION
    assert classify_error(fb_code=1357001) is ErrorKind.SESSION
    assert classify_error(fb_code=368) is ErrorKind.TRANSIENT


def test_with_backoff_retries_then_raises():
    calls = {"n": 0}

    def always_fail():
        calls["n"] += 1
        raise ExtractError("nope", ErrorKind.TRANSIENT)

    with pytest.raises(ExtractError):
        with_backoff(always_fail, max_attempts=3, base=0.0, sleep=lambda s: None)
    assert calls["n"] == 3


def test_with_backoff_returns_on_success():
    calls = {"n": 0}

    def fail_then_ok():
        calls["n"] += 1
        if calls["n"] < 2:
            raise ExtractError("transient", ErrorKind.TRANSIENT)
        return "ok"

    assert with_backoff(fail_then_ok, max_attempts=3, base=0.0, sleep=lambda s: None) == "ok"
    assert calls["n"] == 2


def test_with_backoff_does_not_retry_fatal():
    calls = {"n": 0}

    def fatal():
        calls["n"] += 1
        raise ExtractError("fatal", ErrorKind.FATAL)

    with pytest.raises(ExtractError):
        with_backoff(fatal, max_attempts=3, base=0.0, sleep=lambda s: None)
    assert calls["n"] == 1  # fatal is not retried
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/extract/test_backoff.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agf.extract.backoff'`

- [ ] **Step 3: Write minimal implementation**

`src/agf/extract/backoff.py`:
```python
import random
import time as _time
from collections.abc import Callable
from agf.extract.errors import ErrorKind, ExtractError

_RETRYABLE = {ErrorKind.RATE_LIMIT, ErrorKind.TRANSIENT, ErrorKind.SESSION}


def classify_error(*, status: int | None = None, fb_code: int | None = None) -> ErrorKind:
    """Map an HTTP status or a Facebook GraphQL error code to an ErrorKind."""
    if fb_code is not None:
        if fb_code == 1675004:
            return ErrorKind.RATE_LIMIT
        if fb_code in (1357004, 1357001):
            return ErrorKind.SESSION
        if fb_code == 368:
            return ErrorKind.TRANSIENT
        return ErrorKind.FATAL
    if status is not None:
        if status == 429:
            return ErrorKind.RATE_LIMIT
        if status == 403:
            return ErrorKind.SESSION
        if 500 <= status < 600:
            return ErrorKind.TRANSIENT
    return ErrorKind.FATAL


def with_backoff(fn: Callable, *, max_attempts: int = 5, base: float = 0.5,
                 cap: float = 30.0, sleep: Callable[[float], None] = _time.sleep):
    """Call fn(); retry retryable ExtractErrors with jittered exponential backoff."""
    last: ExtractError | None = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except ExtractError as e:
            last = e
            if e.kind not in _RETRYABLE:
                raise
            if attempt == max_attempts - 1:
                break
            delay = min(cap, base * (2 ** attempt))
            sleep(delay + random.uniform(0, delay))
    raise last if last else ExtractError("backoff exhausted")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/extract/test_backoff.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/agf/extract/backoff.py tests/extract/test_backoff.py
git commit -m "feat(plan-2): error taxonomy + jittered exponential backoff"
```

---

### Task 3: `proxy.py` — `ProxyPool.from_env` with dead-proxy cooldown

**Files:**
- Create: `src/agf/extract/proxy.py`
- Test: `tests/extract/test_proxy.py`

- [ ] **Step 1: Write the failing test**

`tests/extract/test_proxy.py`:
```python
from agf.extract.proxy import ProxyPool


def test_from_env_parses_newline_and_comma(monkeypatch):
    monkeypatch.setenv("AGF_PROXIES", "u:p@host1:1000\nu:p@host2:2000, u:p@host3:3000")
    pool = ProxyPool.from_env()
    assert len(pool) == 3


def test_empty_env_yields_none(monkeypatch):
    monkeypatch.delenv("AGF_PROXIES", raising=False)
    pool = ProxyPool.from_env()
    assert len(pool) == 0
    assert pool.acquire() is None
    assert pool.as_curl_proxies(None) is None


def test_round_robin_rotation():
    pool = ProxyPool(["a:1@h:1", "b:2@h:2"])
    first = pool.acquire()
    second = pool.acquire()
    third = pool.acquire()
    assert {first, second} == {"a:1@h:1", "b:2@h:2"}
    assert third == first  # wrapped around


def test_dead_proxy_skipped_until_cooldown():
    pool = ProxyPool(["a:1@h:1", "b:2@h:2"], max_failures=1, cooldown=100.0)
    now = {"t": 0.0}
    pool._now = lambda: now["t"]
    p = pool.acquire()
    pool.mark_failed(p)                 # 1 failure == max_failures -> dead
    nxt = pool.acquire()
    assert nxt != p                     # dead one skipped
    assert pool.acquire() != p          # still skipped within cooldown
    now["t"] = 200.0                    # past cooldown
    revived = {pool.acquire() for _ in range(4)}
    assert p in revived                 # revived after cooldown


def test_as_curl_proxies_shape():
    pool = ProxyPool(["u:p@h:9"])
    proxies = pool.as_curl_proxies("u:p@h:9")
    assert proxies == {"http": "http://u:p@h:9", "https": "http://u:p@h:9"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/extract/test_proxy.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agf.extract.proxy'`

- [ ] **Step 3: Write minimal implementation**

`src/agf/extract/proxy.py`:
```python
import os
import re
import time as _time
from itertools import cycle


class ProxyPool:
    """Round-robin pool of `user:pass@host:port` proxies with dead-proxy cooldown."""

    def __init__(self, proxies: list[str], max_failures: int = 3, cooldown: float = 300.0):
        self._proxies = list(proxies)
        self._cycle = cycle(self._proxies) if self._proxies else None
        self._max_failures = max_failures
        self._cooldown = cooldown
        self._failures: dict[str, int] = {}
        self._dead_until: dict[str, float] = {}
        self._now = _time.time

    @classmethod
    def from_env(cls, var: str = "AGF_PROXIES", **kw) -> "ProxyPool":
        raw = os.environ.get(var, "") or ""
        parts = [p.strip() for p in re.split(r"[\n,]+", raw) if p.strip()]
        return cls(parts, **kw)

    def __len__(self) -> int:
        return len(self._proxies)

    def _alive(self, proxy: str) -> bool:
        dead_until = self._dead_until.get(proxy)
        return dead_until is None or self._now() >= dead_until

    def acquire(self) -> str | None:
        if not self._cycle:
            return None
        for _ in range(len(self._proxies)):
            candidate = next(self._cycle)
            if self._alive(candidate):
                return candidate
        return None  # all dead

    def mark_failed(self, proxy: str | None) -> None:
        if proxy is None:
            return
        self._failures[proxy] = self._failures.get(proxy, 0) + 1
        if self._failures[proxy] >= self._max_failures:
            self._dead_until[proxy] = self._now() + self._cooldown
            self._failures[proxy] = 0

    def mark_ok(self, proxy: str | None) -> None:
        if proxy is not None:
            self._failures.pop(proxy, None)

    def as_curl_proxies(self, proxy: str | None) -> dict | None:
        """curl_cffi proxies dict, or None for a direct connection."""
        if not proxy:
            return None
        url = proxy if "://" in proxy else f"http://{proxy}"
        return {"http": url, "https": url}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/extract/test_proxy.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/agf/extract/proxy.py tests/extract/test_proxy.py
git commit -m "feat(plan-2): ProxyPool with round-robin + dead-proxy cooldown"
```

---

### Task 4: `rd_challenge.py` — pure-HTTP `rd_challenge` solver

The single most important module: defeats Facebook's `rd_challenge` 403 gate without a browser (yt-dlp #15577/#15582 pattern, proven on-machine 2026-06-04).

**Files:**
- Create: `src/agf/extract/rd_challenge.py`
- Create: `tests/extract/fixtures/rd_challenge_stub.html`
- Test: `tests/extract/test_rd_challenge.py`

- [ ] **Step 1: Create the challenge-stub fixture (the real ~481-byte 403 body shape)**

`tests/extract/fixtures/rd_challenge_stub.html`:
```html
<!DOCTYPE html><html><head><title>Ad Library</title></head><body><script>
(function(){if(document){fetch('/__rd_verify_Q_6hBQNE9Z7FfIhrI6-9hWbT9aaHVKDP8?challenge=3',{method:'POST'}).finally(()=>window.location.reload());}})();
</script></body></html>
```

- [ ] **Step 2: Write the failing test**

`tests/extract/test_rd_challenge.py`:
```python
from pathlib import Path
from agf.extract.rd_challenge import extract_challenge_path, solve_rd_challenge
from tests.extract.conftest import FakeResponse, FakeSession

STUB = (Path(__file__).parent / "fixtures" / "rd_challenge_stub.html").read_text()


def test_extract_challenge_path():
    path = extract_challenge_path(STUB)
    assert path == "/__rd_verify_Q_6hBQNE9Z7FfIhrI6-9hWbT9aaHVKDP8?challenge=3"


def test_extract_challenge_path_absent_returns_none():
    assert extract_challenge_path("<html>nothing here</html>") is None


def test_solve_posts_then_reports_solved():
    session = FakeSession(responses={
        ("POST", "__rd_verify"): [FakeResponse(status_code=200)],
    })
    # simulate the cookie jar being populated by the POST
    def post(url, data=None, headers=None, **kw):
        session.calls.append(("POST", url))
        session.cookies["rd_challenge"] = "COOKIEVAL"
        return FakeResponse(status_code=200)
    session.post = post

    assert solve_rd_challenge(session, STUB) is True
    assert any("POST" == m and "/__rd_verify_" in u for m, u in session.calls)
    assert session.cookies.get("rd_challenge") == "COOKIEVAL"


def test_solve_returns_false_when_no_challenge():
    session = FakeSession()
    assert solve_rd_challenge(session, "<html>real page, no challenge</html>") is False
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/extract/test_rd_challenge.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agf.extract.rd_challenge'`

- [ ] **Step 4: Write minimal implementation**

`src/agf/extract/rd_challenge.py`:
```python
import re

_BASE = "https://www.facebook.com"
# The stub's only logic: fetch('/__rd_verify_<token>?challenge=N', {method:'POST'})
_CHALLENGE_RE = re.compile(r"""fetch\(\s*['"](/__rd_verify_[^'"]+)['"]""")


def extract_challenge_path(html: str) -> str | None:
    """Return the /__rd_verify_...?challenge=N path from a 403 stub, or None."""
    m = _CHALLENGE_RE.search(html or "")
    return m.group(1) if m else None


def solve_rd_challenge(session, html: str) -> bool:
    """If `html` is the rd_challenge stub, POST the verify path on `session`
    (minting the rd_challenge cookie into the session jar) and return True.
    Returns False if there is no challenge to solve.

    `session` is any object with `.post(url, data=...)` and a `.cookies` mapping
    (AdLibrarySession or curl_cffi Session). The caller re-GETs the page after.
    """
    path = extract_challenge_path(html)
    if not path:
        return False
    session.post(_BASE + path, data=b"")
    return True
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/extract/test_rd_challenge.py -q`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add src/agf/extract/rd_challenge.py tests/extract/fixtures/rd_challenge_stub.html tests/extract/test_rd_challenge.py
git commit -m "feat(plan-2): pure-HTTP rd_challenge solver (yt-dlp #15577 pattern)"
```

---

### Task 5: `token_heal.py` — LSD / fb_dtsg / doc_id self-heal

**Files:**
- Create: `src/agf/extract/token_heal.py`
- Create: `tests/extract/fixtures/ads_library_real.html`
- Test: `tests/extract/test_token_heal.py`

- [ ] **Step 1: Create a trimmed real-page fixture (LSD inline; doc_id in a relay-operation bundle stub)**

`tests/extract/fixtures/ads_library_real.html`:
```html
<!doctype html><html><head><script>
["LSD",[],{"token":"AdRAQDUWxPlive"}]
"DTSGInitialData",[],{"token":"NAcMlivetoken123"},258]
__d("AdLibrarySearchPaginationQuery_facebookRelayOperation",[],(function(a){a.exports="25464068859919530"}),null);
</script></head><body></body></html>
```

- [ ] **Step 2: Write the failing test**

`tests/extract/test_token_heal.py`:
```python
from pathlib import Path
from agf.extract.token_heal import (
    SessionTokens, extract_lsd, extract_fb_dtsg, heal_doc_id, heal_from_html,
    FALLBACK_DOC_IDS,
)

HTML = (Path(__file__).parent / "fixtures" / "ads_library_real.html").read_text()


def test_extract_lsd_reuses_plan1_regex():
    assert extract_lsd(HTML) == "AdRAQDUWxPlive"


def test_extract_fb_dtsg():
    assert extract_fb_dtsg(HTML) == "NAcMlivetoken123"


def test_heal_doc_id_from_relay_bundle():
    assert heal_doc_id(HTML) == "25464068859919530"


def test_heal_doc_id_falls_back_when_absent():
    assert heal_doc_id("<html>no bundle</html>") == FALLBACK_DOC_IDS[0]


def test_heal_from_html_returns_full_tokens():
    tokens = heal_from_html(HTML)
    assert isinstance(tokens, SessionTokens)
    assert tokens.lsd == "AdRAQDUWxPlive"
    assert tokens.fb_dtsg == "NAcMlivetoken123"
    assert tokens.doc_id == "25464068859919530"


def test_heal_from_html_raises_without_lsd():
    import pytest
    with pytest.raises(ValueError):
        heal_from_html("<html>challenge stub, no lsd</html>")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/extract/test_token_heal.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agf.extract.token_heal'`

- [ ] **Step 4: Write minimal implementation**

`src/agf/extract/token_heal.py`:
```python
import re
from dataclasses import dataclass
from agf.bootstrap import extract_lsd  # reuse Plan 1 (raises ValueError if absent)

# Known-good fallbacks, freshest first (master-plan §9). doc_id rotates ~2-4 weeks.
FALLBACK_DOC_IDS = ["27201872659451053", "25464068859919530", "33730956806489539"]

_FB_DTSG_RE = re.compile(r'"DTSGInit(?:ial)?Data",\[\],\{"token":"([^"]+)"')
_DOCID_BUNDLE_RE = re.compile(
    r'AdLibrarySearchPaginationQuery_facebookRelayOperation",\[\],'
    r'\(function\([^)]*\)\{a\.exports="(\d+)"'
)
_DOCID_INLINE_RE = re.compile(
    r'AdLibrarySearchPaginationQuery.{0,400}?"doc_id":"(\d+)"', re.DOTALL
)


@dataclass
class SessionTokens:
    lsd: str
    fb_dtsg: str | None
    doc_id: str


def extract_fb_dtsg(html: str) -> str | None:
    """Extract the fb_dtsg CSRF token from page HTML (None if absent)."""
    m = _FB_DTSG_RE.search(html)
    return m.group(1) if m else None


def heal_doc_id(html: str) -> str:
    """Self-heal the AdLibrarySearchPaginationQuery doc_id from the JS bundle or
    inline; fall back to the freshest known-good value if neither is present."""
    m = _DOCID_BUNDLE_RE.search(html) or _DOCID_INLINE_RE.search(html)
    return m.group(1) if m else FALLBACK_DOC_IDS[0]


def heal_from_html(html: str) -> SessionTokens:
    """Build SessionTokens from a real (post-challenge) page. Raises ValueError
    if the LSD token is absent (i.e. the page is still a challenge stub)."""
    return SessionTokens(
        lsd=extract_lsd(html),               # raises ValueError if missing
        fb_dtsg=extract_fb_dtsg(html),
        doc_id=heal_doc_id(html),
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/extract/test_token_heal.py -q`
Expected: PASS (6 passed)

- [ ] **Step 6: Commit**

```bash
git add src/agf/extract/token_heal.py tests/extract/fixtures/ads_library_real.html tests/extract/test_token_heal.py
git commit -m "feat(plan-2): LSD/fb_dtsg/doc_id self-heal with fallbacks"
```

---

### Task 6: `tls_client.py` — `AdLibrarySession` (curl_cffi wrapper)

**Files:**
- Create: `src/agf/extract/tls_client.py`
- Test: `tests/extract/test_tls_client.py`

- [ ] **Step 1: Write the failing test (unit: header builder + warm flow with a fake transport)**

`tests/extract/test_tls_client.py`:
```python
import pytest
from agf.extract.tls_client import build_session_headers, AdLibrarySession
from tests.extract.conftest import FakeResponse


def test_build_session_headers_have_browser_shape():
    h = build_session_headers()
    assert "Mozilla/5.0" in h["User-Agent"]
    assert h["Sec-Fetch-Mode"] == "navigate"
    assert h["Accept-Language"].startswith("en-US")


def test_warm_solves_challenge_then_returns_real_html():
    # transport returns the 403 stub, then (after the verify POST) the real page
    stub = ("<script>fetch('/__rd_verify_TOK?challenge=3',{method:'POST'})"
            ".finally(()=>location.reload());</script>")
    real = '<script>["LSD",[],{"token":"AVlive"}]</script>'
    seq = [FakeResponse(403, stub), FakeResponse(200, real)]
    posted = {"verify": False}

    class FakeTransport:
        cookies = {}
        def get(self, url, **kw):
            return seq.pop(0)
        def post(self, url, data=None, headers=None, **kw):
            posted["verify"] = "/__rd_verify_" in url
            self.cookies["rd_challenge"] = "C"
            return FakeResponse(200)

    sess = AdLibrarySession(transport=FakeTransport(), proxy=None)
    html = sess.warm_get("https://www.facebook.com/ads/library/?q=x")
    assert posted["verify"] is True
    assert '"LSD"' in html
    assert sess.transport.cookies.get("rd_challenge") == "C"


def test_warm_raises_if_challenge_never_clears():
    stub = "<script>fetch('/__rd_verify_T?challenge=3',{method:'POST'})</script>"

    class StubbornTransport:
        cookies = {}
        def get(self, url, **kw):
            return FakeResponse(403, stub)           # always the stub
        def post(self, url, data=None, headers=None, **kw):
            return FakeResponse(200)

    sess = AdLibrarySession(transport=StubbornTransport(), proxy=None)
    with pytest.raises(Exception):
        sess.warm_get("https://www.facebook.com/ads/library/?q=x", max_solves=2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/extract/test_tls_client.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agf.extract.tls_client'`

- [ ] **Step 3: Write minimal implementation**

`src/agf/extract/tls_client.py`:
```python
from agf.extract.errors import ErrorKind, ExtractError
from agf.extract.rd_challenge import solve_rd_challenge, extract_challenge_path

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_LIBRARY = "https://www.facebook.com/ads/library/"
_GRAPHQL = "https://www.facebook.com/api/graphql/"


def build_session_headers() -> dict:
    """Browser-shaped headers to accompany the curl_cffi Chrome TLS fingerprint."""
    return {
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }


def _new_curl_transport(proxy_dict: dict | None):
    """Build a real curl_cffi Session impersonating Chrome (TLS/JA3)."""
    from curl_cffi import requests as cffi  # imported lazily so unit tests need no network dep
    return cffi.Session(impersonate="chrome", timeout=30,
                        headers=build_session_headers(), proxies=proxy_dict)


class AdLibrarySession:
    """Thin wrapper over a curl_cffi Session: warms past rd_challenge and exposes
    `.get`/`.post`. `transport` is injectable for unit tests (duck-typed)."""

    LIBRARY = _LIBRARY
    GRAPHQL = _GRAPHQL

    def __init__(self, *, transport=None, proxy: str | None = None,
                 proxy_dict: dict | None = None):
        self.proxy = proxy
        self.transport = transport if transport is not None else _new_curl_transport(proxy_dict)

    def get(self, url, **kw):
        return self.transport.get(url, **kw)

    def post(self, url, data=None, headers=None, **kw):
        return self.transport.post(url, data=data, headers=headers, **kw)

    def warm_get(self, url: str, *, max_solves: int = 2) -> str:
        """GET `url`, solving rd_challenge as needed; return the real page HTML."""
        resp = self.get(url)
        for _ in range(max_solves + 1):
            if resp.status_code != 403 and extract_challenge_path(resp.text) is None:
                return resp.text
            if not solve_rd_challenge(self.transport, resp.text):
                break
            resp = self.get(url)
        raise ExtractError("rd_challenge not cleared after retries", ErrorKind.SESSION)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/extract/test_tls_client.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Add an opt-in live smoke test**

Append to `tests/extract/test_tls_client.py`:
```python
import pytest


@pytest.mark.integration
def test_live_warm_get_returns_real_page():
    sess = AdLibrarySession(proxy=None)  # real curl_cffi transport
    html = sess.warm_get(
        "https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
        "&country=US&q=workout%20app&media_type=all&search_type=keyword_unordered"
    )
    assert len(html) > 100_000           # real page, not the ~481-byte stub
    assert '"LSD"' in html
```

- [ ] **Step 6: Commit**

```bash
git add src/agf/extract/tls_client.py tests/extract/test_tls_client.py
git commit -m "feat(plan-2): AdLibrarySession (curl_cffi) with rd_challenge warm"
```

---

### Task 7: `search.py` — variables builder + paginated `search_ads`

Extends Plan 1's `build_variables` with the firehose params (`sort_by_impressions`, `search_type`, `view_all_page_id`) WITHOUT mutating the frozen Plan 1 signature, and reuses Plan 1's `parse_search_response`.

**Files:**
- Create: `src/agf/extract/search.py`
- Create: `tests/extract/fixtures/search_page1.json`, `tests/extract/fixtures/search_page2.json`
- Test: `tests/extract/test_search.py`

- [ ] **Step 1: Create two-page response fixtures**

`tests/extract/fixtures/search_page1.json`:
```json
{"data":{"ad_library_main":{"search_results_connection":{
  "edges":[{"node":{"collated_results":[
    {"ad_archive_id":"1","page_id":"P1","page_name":"GemA","is_active":true,
     "start_date":1714500000,"end_date":null,"collation_count":5,
     "snapshot":{"cta_type":"INSTALL_MOBILE_APP","link_url":"https://apps.apple.com/us/app/x/id111","body":{"text":"a"}}}
  ]}}],
  "page_info":{"has_next_page":true,"end_cursor":"CUR1"}}}}}
```

`tests/extract/fixtures/search_page2.json`:
```json
{"data":{"ad_library_main":{"search_results_connection":{
  "edges":[{"node":{"collated_results":[
    {"ad_archive_id":"2","page_id":"P2","page_name":"GemB","is_active":true,
     "start_date":1714600000,"end_date":null,"collation_count":3,
     "snapshot":{"cta_type":"INSTALL_MOBILE_APP","link_url":"https://play.google.com/store/apps/details?id=com.b","body":{"text":"b"}}}
  ]}}],
  "page_info":{"has_next_page":false,"end_cursor":null}}}}}
```

- [ ] **Step 2: Write the failing test**

`tests/extract/test_search.py`:
```python
import json
from pathlib import Path
from agf.extract.search import build_search_variables, search_ads
from agf.extract.token_heal import SessionTokens
from tests.extract.conftest import FakeResponse, FakeSession

FX = Path(__file__).parent / "fixtures"
PAGE1 = (FX / "search_page1.json").read_text()
PAGE2 = (FX / "search_page2.json").read_text()
TOKENS = SessionTokens(lsd="AVlsd", fb_dtsg="dtsg", doc_id="27201872659451053")


def test_build_search_variables_defaults():
    v = build_search_variables(query="workout", countries=["US"])
    assert v["queryString"] == "workout"
    assert v["countries"] == ["US"]
    assert v["searchType"] == "KEYWORD_UNORDERED"
    assert "sortData" not in v
    assert v["viewAllPageID"] == "0"


def test_build_search_variables_impressions_sort_and_page_scope():
    v = build_search_variables(query="", countries=["DE"], sort_by_impressions=True,
                               search_type="page", view_all_page_id="98765")
    assert v["sortData"] == {"mode": "SORT_BY_TOTAL_IMPRESSIONS", "direction": "DESCENDING"}
    assert v["searchType"] == "page"
    assert v["viewAllPageID"] == "98765"


def test_search_ads_paginates_and_collates():
    # GraphQL POSTs return page1 then page2; for(;;); guard included to prove stripping
    session = FakeSession(responses={
        ("POST", "/api/graphql/"): [
            FakeResponse(200, "for (;;);" + PAGE1),
            FakeResponse(200, PAGE2),
        ],
    })
    records, cursor, has_next = search_ads(session, TOKENS, query="x",
                                           countries=["US"], max_pages=5)
    assert [r.ad_archive_id for r in records] == ["1", "2"]
    assert has_next is False
    # exactly two POSTs (stopped when has_next went false)
    assert sum(1 for m, u in session.calls if m == "POST") == 2


def test_search_ads_respects_max_pages():
    session = FakeSession(responses={
        ("POST", "/api/graphql/"): [FakeResponse(200, PAGE1), FakeResponse(200, PAGE1)],
    })
    records, cursor, has_next = search_ads(session, TOKENS, query="x",
                                           countries=["US"], max_pages=1)
    assert len(records) == 1                  # only page 1 fetched
    assert sum(1 for m, u in session.calls if m == "POST") == 1
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/extract/test_search.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agf.extract.search'`

- [ ] **Step 4: Write minimal implementation**

`src/agf/extract/search.py`:
```python
from agf.request import build_variables, build_form_body
from agf.parse import parse_search_response, AdRecord
from agf.fbjson import parse_fb_json
from agf.extract.token_heal import SessionTokens

_GRAPHQL = "https://www.facebook.com/api/graphql/"


def build_search_variables(*, query: str, countries: list[str],
                           active_status: str = "ACTIVE",
                           sort_by_impressions: bool = False,
                           search_type: str = "KEYWORD_UNORDERED",
                           view_all_page_id: str | None = None,
                           cursor: str | None = None) -> dict:
    """Build the AdLibrarySearchPaginationQuery `variables`, extending the Plan 1
    base with firehose-B (impressions sort) and EU->US bridge (page-scope) params."""
    v = build_variables(query=query, countries=countries, cursor=cursor)
    v["activeStatus"] = active_status
    v["searchType"] = search_type
    if view_all_page_id is not None:
        v["viewAllPageID"] = view_all_page_id
    if sort_by_impressions:
        v["sortData"] = {"mode": "SORT_BY_TOTAL_IMPRESSIONS", "direction": "DESCENDING"}
    return v


def search_ads(session, tokens: SessionTokens, *, query: str, countries: list[str],
               active_status: str = "ACTIVE", sort_by_impressions: bool = False,
               search_type: str = "KEYWORD_UNORDERED", view_all_page_id: str | None = None,
               max_pages: int = 10) -> tuple[list[AdRecord], str | None, bool]:
    """Page through an Ad Library search via `session` (curl_cffi-backed); collate ads.

    Returns (records, last_cursor, has_next). Reuses sessionID+collationToken across
    pages by mutating only the cursor (Plan 1 build_variables seeds fresh ids once)."""
    out: list[AdRecord] = []
    base = build_search_variables(query=query, countries=countries,
                                  active_status=active_status,
                                  sort_by_impressions=sort_by_impressions,
                                  search_type=search_type, view_all_page_id=view_all_page_id)
    cursor: str | None = None
    has_next = False
    for _ in range(max_pages):
        variables = dict(base)
        if cursor is not None:
            variables["cursor"] = cursor
        body = build_form_body(lsd=tokens.lsd, variables=variables, doc_id=tokens.doc_id)
        resp = session.post(_GRAPHQL, data=body, headers={
            "x-fb-lsd": tokens.lsd,
            "x-fb-friendly-name": "AdLibrarySearchPaginationQuery",
            "content-type": "application/x-www-form-urlencoded",
        })
        records, cursor, has_next = parse_search_response(parse_fb_json(resp.text))
        out.extend(records)
        if not has_next or not cursor:
            break
    return out, cursor, has_next
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/extract/test_search.py -q`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add src/agf/extract/search.py tests/extract/fixtures/search_page1.json tests/extract/fixtures/search_page2.json tests/extract/test_search.py
git commit -m "feat(plan-2): paginated search_ads with impressions-sort + page-scope params"
```

---

### Task 8: `ad_details.py` — per-ad EU reach + active

**Files:**
- Create: `src/agf/extract/ad_details.py`
- Create: `tests/extract/fixtures/ad_details.json`
- Test: `tests/extract/test_ad_details.py`

- [ ] **Step 1: Create the ad-details fixture**

`tests/extract/fixtures/ad_details.json`:
```json
{"data":{"ad_library_main":{"ad_details":{"aaa_info":{"eu_total_reach":12345,"is_active":true}}}}}
```

- [ ] **Step 2: Write the failing test**

`tests/extract/test_ad_details.py`:
```python
import json
from pathlib import Path
from agf.extract.ad_details import AdDetails, parse_ad_details, fetch_ad_details
from agf.extract.token_heal import SessionTokens
from tests.extract.conftest import FakeResponse, FakeSession

DATA = json.loads((Path(__file__).parent / "fixtures" / "ad_details.json").read_text())
TOKENS = SessionTokens(lsd="AVlsd", fb_dtsg="dtsg", doc_id="DOCDETAILS")


def test_parse_ad_details_reads_eu_reach_and_active():
    d = parse_ad_details(DATA)
    assert d == AdDetails(eu_total_reach=12345, active=True)


def test_parse_ad_details_absent_reach_is_none():
    d = parse_ad_details({"data": {"ad_library_main": {"ad_details": {"aaa_info": {}}}}})
    assert d.eu_total_reach is None


def test_fetch_ad_details_posts_and_parses():
    session = FakeSession(responses={
        ("POST", "/api/graphql/"): [FakeResponse(200, "for (;;);" + json.dumps(DATA))],
    })
    d = fetch_ad_details(session, TOKENS, "999")
    assert d.eu_total_reach == 12345 and d.active is True
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/extract/test_ad_details.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agf.extract.ad_details'`

- [ ] **Step 4: Write minimal implementation**

`src/agf/extract/ad_details.py`:
```python
import json
from dataclasses import dataclass
from agf.fbjson import parse_fb_json
from agf.request import build_form_body
from agf.extract.token_heal import SessionTokens

_GRAPHQL = "https://www.facebook.com/api/graphql/"
_FRIENDLY = "AdLibraryAdDetailsV2Query"


@dataclass
class AdDetails:
    eu_total_reach: int | None
    active: bool


def parse_ad_details(data: dict) -> AdDetails:
    """Pull aaa_info.eu_total_reach + is_active from an AdDetails response."""
    main = data.get("data", {}).get("ad_library_main", {}) or {}
    details = main.get("ad_details") or {}
    aaa = details.get("aaa_info") or {}
    reach = aaa.get("eu_total_reach")
    return AdDetails(
        eu_total_reach=int(reach) if reach is not None else None,
        active=bool(aaa.get("is_active")),
    )


def fetch_ad_details(session, tokens: SessionTokens, ad_archive_id) -> AdDetails:
    """POST AdLibraryAdDetailsV2Query for one ad and parse the EU reach + active."""
    variables = {"adArchiveID": str(ad_archive_id), "country": "ALL"}
    body = build_form_body(lsd=tokens.lsd, variables=variables, doc_id=tokens.doc_id)
    body["fb_api_req_friendly_name"] = _FRIENDLY
    resp = session.post(_GRAPHQL, data=body, headers={
        "x-fb-lsd": tokens.lsd,
        "x-fb-friendly-name": _FRIENDLY,
        "content-type": "application/x-www-form-urlencoded",
    })
    return parse_ad_details(parse_fb_json(resp.text))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/extract/test_ad_details.py -q`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add src/agf/extract/ad_details.py tests/extract/fixtures/ad_details.json tests/extract/test_ad_details.py
git commit -m "feat(plan-2): ad_details EU-reach + active parser/fetcher"
```

---

### Task 9: `deeplink.py` — iOS-UA redirect resolver

**Files:**
- Create: `src/agf/extract/deeplink.py`
- Test: `tests/extract/test_deeplink.py`

- [ ] **Step 1: Write the failing test**

`tests/extract/test_deeplink.py`:
```python
from agf.extract.deeplink import resolve_deeplink, ResolvedLink
from agf.classify import AppIntent
from tests.extract.conftest import FakeResponse


class RedirectSession:
    """Returns a response whose .url is the final resolved URL (curl_cffi follows
    redirects and exposes the final .url)."""
    def __init__(self, final_url):
        self.final_url = final_url
        self.last_headers = None
    def get(self, url, headers=None, **kw):
        self.last_headers = headers or {}
        r = FakeResponse(200, "")
        r.url = self.final_url
        return r


def test_resolves_mmp_link_to_ios_store():
    session = RedirectSession("https://apps.apple.com/us/app/x/id42")
    out = resolve_deeplink(session, "https://myapp.app.link/abc", ua="ios")
    assert out == ResolvedLink(
        final_url="https://apps.apple.com/us/app/x/id42",
        intent=AppIntent(kind="ios", store_id="42"),
    )
    # iOS UA must have been sent so the MMP serves the App Store branch
    assert "iPhone" in session.last_headers.get("User-Agent", "")


def test_resolves_web_funnel():
    session = RedirectSession("https://getgem.com/quiz")
    out = resolve_deeplink(session, "https://getgem.com/quiz")
    assert out.intent == AppIntent(kind="web", store_id=None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/extract/test_deeplink.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agf.extract.deeplink'`

- [ ] **Step 3: Write minimal implementation**

`src/agf/extract/deeplink.py`:
```python
from dataclasses import dataclass
from agf.classify import classify_url, AppIntent

_IOS_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
           "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")
_DESKTOP_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


@dataclass
class ResolvedLink:
    final_url: str
    intent: AppIntent


def resolve_deeplink(session, url: str, ua: str = "ios") -> ResolvedLink:
    """Follow an MMP/deeplink (app.link/onelink.to/apple.co/...) with an iOS UA so
    it serves its App Store branch, then classify the final URL. `session` follows
    redirects and exposes the final `.url` (curl_cffi does by default)."""
    headers = {"User-Agent": _IOS_UA if ua == "ios" else _DESKTOP_UA}
    resp = session.get(url, headers=headers)
    final_url = getattr(resp, "url", None) or url
    return ResolvedLink(final_url=str(final_url), intent=classify_url(str(final_url)))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/extract/test_deeplink.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/agf/extract/deeplink.py tests/extract/test_deeplink.py
git commit -m "feat(plan-2): iOS-UA deeplink resolver -> AppIntent"
```

---

### Task 10: `session_pool.py` — warm-once session with staleness/re-warm

**Files:**
- Create: `src/agf/extract/session_pool.py`
- Test: `tests/extract/test_session_pool.py`

- [ ] **Step 1: Write the failing test**

`tests/extract/test_session_pool.py`:
```python
from agf.extract.session_pool import WarmSession, SessionPool
from agf.extract.token_heal import SessionTokens


def _real_html():
    return ('<script>["LSD",[],{"token":"AVw"}]'
            '__d("AdLibrarySearchPaginationQuery_facebookRelayOperation",[],'
            '(function(a){a.exports="27201872659451053"}),null);</script>')


def test_warm_session_is_stale_after_max_age():
    clock = {"t": 1000.0}
    ws = WarmSession(session=object(), tokens=SessionTokens("a", None, "d"),
                     created_at=1000.0, max_age=1800.0, now=lambda: clock["t"])
    assert ws.is_stale() is False
    clock["t"] = 1000.0 + 1801
    assert ws.is_stale() is True


def test_pool_warms_once_then_reuses(monkeypatch):
    warms = {"n": 0}

    class FakeAdLibSession:
        def warm_get(self, url, **kw):
            warms["n"] += 1
            return _real_html()

    pool = SessionPool(make_session=lambda: FakeAdLibSession(), max_age=1800.0)
    pool._now = lambda: 0.0
    a = pool.acquire()
    b = pool.acquire()
    assert a is b                 # reused, not re-warmed
    assert warms["n"] == 1
    assert a.tokens.doc_id == "27201872659451053"


def test_pool_rewarms_when_stale():
    warms = {"n": 0}

    class FakeAdLibSession:
        def warm_get(self, url, **kw):
            warms["n"] += 1
            return _real_html()

    clock = {"t": 0.0}
    pool = SessionPool(make_session=lambda: FakeAdLibSession(), max_age=1800.0)
    pool._now = lambda: clock["t"]
    pool.acquire()
    clock["t"] = 2000.0           # past max_age
    pool.acquire()
    assert warms["n"] == 2        # re-warmed


def test_pool_invalidate_forces_rewarm():
    warms = {"n": 0}

    class FakeAdLibSession:
        def warm_get(self, url, **kw):
            warms["n"] += 1
            return _real_html()

    pool = SessionPool(make_session=lambda: FakeAdLibSession(), max_age=1800.0)
    pool._now = lambda: 0.0
    pool.acquire()
    pool.invalidate()             # e.g. after a 403/session error mid-stream
    pool.acquire()
    assert warms["n"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/extract/test_session_pool.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agf.extract.session_pool'`

- [ ] **Step 3: Write minimal implementation**

`src/agf/extract/session_pool.py`:
```python
import time as _time
from dataclasses import dataclass
from agf.extract.token_heal import SessionTokens, heal_from_html

_SEARCH_URL = ("https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
               "&country=US&q=&media_type=all&search_type=keyword_unordered")


@dataclass
class WarmSession:
    session: object              # AdLibrarySession
    tokens: SessionTokens
    created_at: float
    max_age: float = 1800.0      # MetaAdsCollector default: re-warm after 30 min
    now: object = _time.time

    def is_stale(self) -> bool:
        return (self.now() - self.created_at) > self.max_age


class SessionPool:
    """Warm-once / reuse-many session manager. Holds a single WarmSession and
    re-warms when it goes stale or is invalidated (after a 403/session error)."""

    def __init__(self, make_session, *, max_age: float = 1800.0,
                 warm_url: str = _SEARCH_URL):
        self._make_session = make_session
        self._max_age = max_age
        self._warm_url = warm_url
        self._current: WarmSession | None = None
        self._now = _time.time

    def _warm(self) -> WarmSession:
        session = self._make_session()
        html = session.warm_get(self._warm_url)
        tokens = heal_from_html(html)
        return WarmSession(session=session, tokens=tokens, created_at=self._now(),
                           max_age=self._max_age, now=self._now)

    def acquire(self) -> WarmSession:
        if self._current is None or self._current.is_stale():
            self._current = self._warm()
        return self._current

    def invalidate(self) -> None:
        """Drop the current session so the next acquire() re-warms (call on a
        403 + rd_challenge, FB session error 1357004/1357001, or HTTP 400)."""
        self._current = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/extract/test_session_pool.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/agf/extract/session_pool.py tests/extract/test_session_pool.py
git commit -m "feat(plan-2): SessionPool warm-once/reuse with staleness + invalidate"
```

---

### Task 11: `warmer.py` — OPTIONAL Playwright/Camoufox standby warmer

A documented fallback for the rare case the pure-HTTP `rd_challenge` solve stops working (e.g. Meta upgrades it to a genuine computed challenge). NOT imported by the hot path; covered by an opt-in integration test only.

**Files:**
- Create: `src/agf/extract/warmer.py`
- Test: `tests/extract/test_warmer.py`

- [ ] **Step 1: Write the failing test (unit: it must NOT import playwright at module load)**

`tests/extract/test_warmer.py`:
```python
import importlib
import pytest


def test_warmer_module_imports_without_playwright():
    # The module must import even when playwright is absent (lazy import inside fn).
    mod = importlib.import_module("agf.extract.warmer")
    assert hasattr(mod, "browser_warm")


@pytest.mark.integration
def test_browser_warm_returns_tokens():
    from agf.extract.warmer import browser_warm
    tokens = browser_warm(
        "https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
        "&country=US&q=workout%20app&media_type=all&search_type=keyword_unordered"
    )
    assert tokens.lsd and tokens.doc_id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/extract/test_warmer.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agf.extract.warmer'`

- [ ] **Step 3: Write minimal implementation**

`src/agf/extract/warmer.py`:
```python
"""OPTIONAL standby warmer. Uses a real browser to load the Ad Library page and
harvest tokens when the pure-HTTP rd_challenge path (Task 4/6) fails. Playwright is
imported lazily so the package works without it installed."""
from agf.extract.token_heal import heal_from_html


def browser_warm(url: str, *, proxy: str | None = None, timeout_ms: int = 30_000):
    """Launch a headless browser, load `url` (the browser runs the rd_challenge JS),
    and return SessionTokens parsed from the rendered HTML.

    Requires `playwright` + `playwright install chromium`. Swap in Camoufox here if
    Chromium gets detected (see research doc §2)."""
    from playwright.sync_api import sync_playwright  # lazy: optional dependency

    launch_args = {"headless": True}
    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_args)
        ctx_kw = {}
        if proxy:
            ctx_kw["proxy"] = {"server": f"http://{proxy.split('@')[-1]}",
                               **({} if "@" not in proxy else dict(
                                   zip(("username", "password"), proxy.split("@")[0].split(":"))))}
        context = browser.new_context(**ctx_kw)
        page = context.new_page()
        page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)  # let the rd_challenge JS run + reload
        html = page.content()
        browser.close()
    return heal_from_html(html)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/extract/test_warmer.py -q`
Expected: PASS (1 passed, 1 deselected — the integration test is opt-in)

- [ ] **Step 5: Commit**

```bash
git add src/agf/extract/warmer.py tests/extract/test_warmer.py
git commit -m "feat(plan-2): optional Playwright standby warmer (lazy import)"
```

---

### Task 12: `facade.py` — the `Extractor` (§3.4 frozen contract)

**Files:**
- Create: `src/agf/extract/facade.py`
- Test: `tests/extract/test_facade.py`

- [ ] **Step 1: Write the failing test (unit: async facade delegates to injected pool/funcs)**

`tests/extract/test_facade.py`:
```python
import asyncio
from agf.extract.facade import Extractor
from agf.extract.token_heal import SessionTokens
from agf.parse import AdRecord
from agf.classify import AppIntent
from agf.extract.ad_details import AdDetails
from agf.extract.deeplink import ResolvedLink


class StubPool:
    def __init__(self):
        self.invalidated = False
        self._ws = type("WS", (), {"session": object(),
                                   "tokens": SessionTokens("L", "D", "27201872659451053")})()
    def acquire(self):
        return self._ws
    def invalidate(self):
        self.invalidated = True


def _ad(i):
    return AdRecord(i, "P", "N", True, 1, None, 1, "X", "x",
                    "https://apps.apple.com/us/app/x/id1", "b")


def test_search_delegates_and_returns_records(monkeypatch):
    captured = {}

    def fake_search_ads(session, tokens, **kw):
        captured.update(kw)
        return [_ad("1"), _ad("2")], None, False

    monkeypatch.setattr("agf.extract.facade.search_ads", fake_search_ads)
    ex = Extractor(pool=StubPool())
    out = asyncio.run(ex.search(query="workout", countries=["US"],
                                sort_by_impressions=True, max_pages=3))
    assert [r.ad_archive_id for r in out] == ["1", "2"]
    assert captured["sort_by_impressions"] is True
    assert captured["max_pages"] == 3


def test_ad_details_delegates(monkeypatch):
    monkeypatch.setattr("agf.extract.facade.fetch_ad_details",
                        lambda s, t, aid: AdDetails(eu_total_reach=7, active=True))
    ex = Extractor(pool=StubPool())
    d = asyncio.run(ex.ad_details("999"))
    assert d.eu_total_reach == 7


def test_resolve_deeplink_delegates(monkeypatch):
    monkeypatch.setattr("agf.extract.facade.resolve_deeplink",
                        lambda s, url, ua="ios": ResolvedLink(url, AppIntent("web", None)))
    ex = Extractor(pool=StubPool())
    r = asyncio.run(ex.resolve_deeplink("https://x.com"))
    assert r.intent.kind == "web"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/extract/test_facade.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agf.extract.facade'`

- [ ] **Step 3: Write minimal implementation**

`src/agf/extract/facade.py`:
```python
import asyncio
from agf.parse import AdRecord
from agf.extract.ad_details import AdDetails, fetch_ad_details
from agf.extract.deeplink import ResolvedLink, resolve_deeplink
from agf.extract.search import search_ads
from agf.extract.session_pool import SessionPool
from agf.extract.tls_client import AdLibrarySession
from agf.extract.proxy import ProxyPool


class Extractor:
    """The §3.4 facade: manages the warm-session lifecycle so Plans 4/5/6 never
    touch sessions/tokens. Sync work runs in a thread so the API stays async."""

    def __init__(self, *, pool: SessionPool | None = None, proxies: ProxyPool | None = None):
        if pool is None:
            proxies = proxies or ProxyPool.from_env()
            def make_session():
                proxy = proxies.acquire()
                return AdLibrarySession(proxy=proxy, proxy_dict=proxies.as_curl_proxies(proxy))
            pool = SessionPool(make_session=make_session)
        self._pool = pool

    async def search(self, *, query="", countries=["US"], active_status="ACTIVE",
                     sort_by_impressions=False, search_type="KEYWORD_UNORDERED",
                     view_all_page_id=None, max_pages=10) -> list[AdRecord]:
        def run():
            ws = self._pool.acquire()
            records, _cursor, _has_next = search_ads(
                ws.session, ws.tokens, query=query, countries=countries,
                active_status=active_status, sort_by_impressions=sort_by_impressions,
                search_type=search_type, view_all_page_id=view_all_page_id, max_pages=max_pages)
            return records
        return await asyncio.to_thread(run)

    async def ad_details(self, ad_archive_id) -> AdDetails:
        def run():
            ws = self._pool.acquire()
            return fetch_ad_details(ws.session, ws.tokens, ad_archive_id)
        return await asyncio.to_thread(run)

    async def resolve_deeplink(self, url, ua="ios") -> ResolvedLink:
        def run():
            ws = self._pool.acquire()
            return resolve_deeplink(ws.session, url, ua=ua)
        return await asyncio.to_thread(run)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/extract/test_facade.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Add the opt-in live end-to-end smoke test**

`tests/extract/test_facade.py` (append):
```python
import pytest


@pytest.mark.integration
def test_live_search_returns_real_ads():
    ex = Extractor()  # real curl_cffi + rd_challenge + pool, proxies from AGF_PROXIES
    ads = asyncio.run(ex.search(query="workout app", countries=["US"], max_pages=1))
    assert len(ads) > 0
    assert any(a.page_name for a in ads)
```

- [ ] **Step 6: Commit**

```bash
git add src/agf/extract/facade.py tests/extract/test_facade.py
git commit -m "feat(plan-2): Extractor facade (§3.4) over the warm-session pool"
```

---

## Phase-Verify (Plan 2 done criterion)

- [ ] `cd emerging-app-finder && .venv/bin/pip install -r requirements.txt` (installs curl_cffi)
- [ ] `.venv/bin/pytest tests/extract -q` → all unit tests green, integration deselected
- [ ] Opt-in live chain: `.venv/bin/pytest -m integration tests/extract -q` → live warm (GET→solve rd_challenge→200) → US search returns ≥1 ad → ad_details parses. (Needs current `doc_id`; self-heal + fallbacks cover rotation.)
- [ ] `import agf.extract.facade` works with **no** playwright installed (hot path is curl_cffi-only).
- [ ] Update master-plan §5 dashboard: Plan 2 → ✅, unblock Plan 4.

---

## Risks (carried from research)

- **`doc_id` rotation (~2-4 wks):** `heal_doc_id` reads the JS bundle each warm; fallbacks `27201872659451053`/`25464068859919530`/`33730956806489539` cover gaps. On a GraphQL `400`/`doc_id` error, `SessionPool.invalidate()` forces a re-warm.
- **`rd_challenge` upgraded to a real challenge:** the optional Task 11 browser warmer is the escape hatch (swap Chromium→Camoufox if detected).
- **TLS/IP at volume:** curl_cffi covers TLS; residential proxies (via `AGF_PROXIES`) cover IP — pin one proxy per warm session, never rotate mid-session.
- **Session staleness signals to wire into callers:** 403+`__rd_verify`, 429, FB codes 1357004/1357001 (session) and 1675004 (rate limit), HTTP 400 (stale doc_id/dtsg), empty edges on a known-good query → `pool.invalidate()` + backoff.

---

## Self-review (completed)

- **Spec coverage (master-plan Plan 2 §6 + §3.4):** `Extractor` facade with `search`/`ad_details`/`resolve_deeplink` (Task 12) matching §3.4 signatures incl. `sort_by_impressions`/`search_type`/`view_all_page_id` (Task 7); `with_backoff`/`classify_error` (Task 2); `ProxyPool.from_env(AGF_PROXIES)` (Task 3); token+doc_id self-heal HTML+bundle (Task 5); `AdLibrarySession` (Task 6); `AdDetails` (Task 8); `ResolvedLink` (Task 9). New vs original: `solve_rd_challenge` (Task 4) + `SessionPool` (Task 10) replace `playwright_session.py`; Playwright demoted to optional `warmer.py` (Task 11).
- **Type consistency:** `SessionTokens(lsd, fb_dtsg, doc_id)` is produced by `heal_from_html` (T5) and consumed unchanged by `search_ads` (T7), `fetch_ad_details` (T8), and the facade (T12). `WarmSession.session`/`.tokens` (T10) are read by the facade. `AdRecord`/`AppIntent` are Plan 1 imports, never redeclared.
- **Placeholder scan:** no TBD/TODO; every code step shows complete code; the only "optional" item (Playwright warmer) is fully implemented with a lazy import and an explicit dependency note.
- **DRY:** reuses Plan 1 `build_variables`/`build_form_body`/`parse_search_response`/`parse_fb_json`/`classify_url`/`extract_lsd` rather than reimplementing.
