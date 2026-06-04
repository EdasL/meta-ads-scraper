from pathlib import Path

OPS = Path(__file__).resolve().parents[2] / "ops"


def test_runbook_covers_recovery_paths():
    rb = (OPS / "RUNBOOK.md").read_text()
    for needle in ("doc_id", "rd_challenge", "token_heal", "AGF_PROXIES",
                   "refresh_matview.sh", "backup.sh", "restore",
                   "27201872659451053", "make_enrich_handler"):
        assert needle in rb, needle


def test_proxy_gateway_references_proxy_env():
    assert "AGF_PROXIES" in (OPS / "proxy" / "gateway.conf").read_text()
