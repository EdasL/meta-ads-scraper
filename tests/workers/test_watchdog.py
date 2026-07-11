"""The in-process watchdog that recovers a wedged worker (a degraded-proxy sweep
death-march, or any stage that stops touching the heartbeat). KeepAlive only restarts
on process EXIT, and a hung process never exits — so a daemon thread polls the heartbeat
and SIGKILLs the process when it goes stale, letting launchd restart it.

Pure unit tests: no DB, no real Meta traffic, no real process kill (on_stale injected)."""
import threading
import time

import types

from agf.workers.main import (_heartbeat_stale, _run_watchdog, _touch_heartbeat,
                              _watchdog_max_stale, start_watchdog,
                              _call_with_ceiling, prefetch_creative_urls)
from agf.extract.tls_client import session_timeout


# --- heartbeat staleness -----------------------------------------------------
def test_heartbeat_stale_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("AGF_HEARTBEAT_FILE", str(tmp_path / "absent.heartbeat"))
    assert _heartbeat_stale(100) is True


def test_heartbeat_fresh_after_touch(tmp_path, monkeypatch):
    monkeypatch.setenv("AGF_HEARTBEAT_FILE", str(tmp_path / "hb.heartbeat"))
    _touch_heartbeat()
    assert _heartbeat_stale(100) is False


def test_heartbeat_stale_once_budget_exceeded(tmp_path, monkeypatch):
    monkeypatch.setenv("AGF_HEARTBEAT_FILE", str(tmp_path / "hb.heartbeat"))
    _touch_heartbeat()
    # A clock 1000s in the future, past a 100s budget -> stale.
    assert _heartbeat_stale(100, now=lambda: time.time() + 1000) is True


def test_watchdog_budget_clears_a_full_cycle(monkeypatch):
    # The budget must clear one whole sweep->funnel->enrich cycle plus slack, never
    # firing on a healthy-but-slow worker. Reuses the --healthcheck staleness rule.
    monkeypatch.delenv("AGF_HEALTHCHECK_MAX_STALE_SEC", raising=False)
    monkeypatch.setenv("AGF_SWEEP_INTERVAL_SEC", "60")
    assert _watchdog_max_stale() >= 3600


# --- watchdog loop -----------------------------------------------------------
def test_watchdog_kills_when_stale():
    calls = []
    _run_watchdog(is_stale=lambda: True, on_stale=lambda: calls.append("kill"),
                  poll_sec=0, sleep=lambda s: None, stop=lambda: False)
    assert calls == ["kill"]


def test_watchdog_leaves_healthy_worker_alone():
    calls = []
    ticks = {"n": 0}

    def stop():
        ticks["n"] += 1
        return ticks["n"] > 3        # let it poll a few times, then break

    _run_watchdog(is_stale=lambda: False, on_stale=lambda: calls.append("kill"),
                  poll_sec=0, sleep=lambda s: None, stop=stop)
    assert calls == []


def test_start_watchdog_fires_on_stale_in_daemon_thread():
    fired = threading.Event()
    t = start_watchdog(is_stale=lambda: True, on_stale=fired.set, poll_sec=0.001)
    assert isinstance(t, threading.Thread)
    assert t.daemon is True           # must die with the process, never block exit
    assert fired.wait(2.0) is True


# --- Meta session socket timeout --------------------------------------------
def test_session_timeout_default_total_unchanged(monkeypatch):
    # Explicit (connect, read) tuple; default total (connect+read) stays 30s so a
    # legitimately slow Meta page isn't cut off, but the connect phase is now bounded.
    monkeypatch.delenv("AGF_META_CONNECT_TIMEOUT", raising=False)
    monkeypatch.delenv("AGF_META_READ_TIMEOUT", raising=False)
    connect, read = session_timeout()
    assert (connect, read) == (10.0, 20.0)


def test_session_timeout_env_override(monkeypatch):
    monkeypatch.setenv("AGF_META_CONNECT_TIMEOUT", "5")
    monkeypatch.setenv("AGF_META_READ_TIMEOUT", "12")
    assert session_timeout() == (5.0, 12.0)


# --- creative-prefetch wall-clock ceiling -----------------------------------
# A Meta search egresses through a proxy CONNECT tunnel; libcurl's TIMEOUT_MS does not
# reliably bound a proxy that stalls the tunnel, so the 2026-07 wedge parked the single
# worker process in epoll_wait for hours. The ceiling must abandon a stuck prefetch and
# let the loop continue instead of freezing.
class _FakeScalar:
    def __init__(self, v):
        self._v = v

    def scalar(self):
        return self._v


class _FakeConn:
    """Duck-typed conn: every SELECT page_id returns a truthy page id."""
    def execute(self, *a, **k):
        return _FakeScalar("PAGE")


class _HangingExtractor:
    """search() blocks until released — simulates a stuck proxy tunnel that never
    returns (the real hang was an uncancellable C call in curl, so we block hard)."""
    def __init__(self, release):
        self._release = release

    async def search(self, **kw):
        self._release.wait()      # blocks the daemon thread's event loop
        return []


class _FastExtractor:
    async def search(self, **kw):
        ad = types.SimpleNamespace(ad_archive_id="A1",
                                   image_urls=["http://img"], video_urls=[])
        return [ad]


def test_call_with_ceiling_raises_on_overrun():
    def never_returns():
        threading.Event().wait()   # blocks forever (daemon thread abandoned on timeout)
    t0 = time.time()
    try:
        _call_with_ceiling(never_returns, 0.2, label="probe")
        assert False, "expected TimeoutError"
    except TimeoutError:
        pass
    assert time.time() - t0 < 3.0   # returned control promptly, did not hang


def test_call_with_ceiling_propagates_result_and_errors():
    assert _call_with_ceiling(lambda: 42, 5, label="ok") == 42
    try:
        _call_with_ceiling(lambda: (_ for _ in ()).throw(ValueError("boom")), 5, label="err")
        assert False, "expected ValueError to propagate"
    except ValueError as e:
        assert str(e) == "boom"


def test_prefetch_ceiling_skips_a_stuck_advertiser_without_hanging(monkeypatch):
    monkeypatch.setenv("AGF_PREFETCH_TIMEOUT_SEC", "0.3")
    release = threading.Event()
    try:
        t0 = time.time()
        out = prefetch_creative_urls(_HangingExtractor(release), _FakeConn(),
                                     [1], max_pages=1)
        elapsed = time.time() - t0
        assert out == {}               # stuck advertiser dropped, not hung
        assert elapsed < 5.0           # the worker loop was never frozen
    finally:
        release.set()                  # let the abandoned daemon thread exit cleanly


def test_prefetch_collects_urls_on_success(monkeypatch):
    monkeypatch.setenv("AGF_PREFETCH_TIMEOUT_SEC", "10")
    out = prefetch_creative_urls(_FastExtractor(), _FakeConn(), [7], max_pages=1)
    assert out == {7: {"A1": [("image", "http://img")]}}


class _DeadlineExtractor:
    """search raises the process-wide enrich deadline (ferried through the ceiling
    thread) — simulating SIGALRM firing while the main thread waits in prefetch."""
    async def search(self, **kw):
        from agf.enrich_full.enrich_one import EnrichDeadline
        raise EnrichDeadline("enrich exceeded 180s")


def test_prefetch_does_not_swallow_enrich_deadline(monkeypatch):
    # The 10173 hot-loop (2026-07-11): the alarm's TimeoutError landed inside
    # prefetch's except (ExtractError, TimeoutError) and was treated as a routine
    # per-gem skip, leaving the rest of the enrich UNGUARDED (one-shot alarm) so
    # it ran to the daemon's hard kill. The deadline is a BaseException subclass
    # precisely so no local network-failure handler can eat it.
    import pytest
    from agf.enrich_full.enrich_one import EnrichDeadline
    monkeypatch.setenv("AGF_PREFETCH_TIMEOUT_SEC", "10")
    with pytest.raises(EnrichDeadline):
        prefetch_creative_urls(_DeadlineExtractor(), _FakeConn(), [1], max_pages=1)
