"""Parser + credential-refusal tests for scripts/benchmark_sweep_pool.py.

The benchmark itself is a live tool (real extractors, real DB); these tests only
pin the CLI contract: required arguments, keyword-list parsing, and the refusal
to run without AGF_PROXIES so a bare host IP never eats a rate-limit ban by
accident.
"""
import pytest

from scripts.benchmark_sweep_pool import main, parse_args


def test_required_arguments():
    with pytest.raises(SystemExit):
        parse_args([])
    with pytest.raises(SystemExit):
        parse_args(["--keywords", "meal planner"])          # missing pages/workers


def test_parses_keyword_list_and_numbers():
    args = parse_args(["--keywords", "meal planner, habit tracker",
                       "--pages", "3", "--workers", "2"])
    assert args.keywords == ["meal planner", "habit tracker"]
    assert args.pages == 3
    assert args.workers == 2


def test_refuses_without_proxies(monkeypatch, capsys):
    monkeypatch.delenv("AGF_PROXIES", raising=False)
    code = main(["--keywords", "meal planner", "--pages", "1", "--workers", "1"])
    assert code == 2
    assert "AGF_PROXIES" in capsys.readouterr().err


def test_allow_direct_overrides_proxy_refusal(monkeypatch, capsys):
    monkeypatch.delenv("AGF_PROXIES", raising=False)
    monkeypatch.delenv("AGF_DATABASE_URL", raising=False)
    monkeypatch.delenv("AGF_TEST_DB_URL", raising=False)
    # With --allow-direct the proxy gate passes; the next gate (no DB URL) trips,
    # proving refusal order without touching the network.
    code = main(["--keywords", "meal planner", "--pages", "1", "--workers", "1",
                 "--allow-direct"])
    assert code == 2
    assert "database" in capsys.readouterr().err.lower()
