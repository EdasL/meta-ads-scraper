"""The in-process watchdog that recovers a wedged worker (a degraded-proxy sweep
death-march, or any stage that stops touching the heartbeat). KeepAlive only restarts
on process EXIT, and a hung process never exits — so a daemon thread polls the heartbeat
and SIGKILLs the process when it goes stale, letting launchd restart it.

Pure unit tests: no DB, no real Meta traffic, no real process kill (on_stale injected)."""
import threading
import time

from agf.workers.main import (_heartbeat_stale, _run_watchdog, _touch_heartbeat,
                              _watchdog_max_stale, start_watchdog)
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
