import json
import shutil
from pathlib import Path
import pytest
from agf.enrich_full.appstore import lookup_google_play, _SIDECAR


def test_parses_sidecar_json_into_appmeta():
    def fake_runner(ids):
        return json.dumps([
            {"appId": "com.gem.app", "title": "Gem", "developer": "Acme",
             "genre": "Health & Fitness", "released": "Jun 3, 2026",
             "releaseDateISO": "2026-06-03", "score": 4.6, "ratings": 321},
        ])
    out = lookup_google_play(["com.gem.app"], runner=fake_runner)
    assert set(out) == {"com.gem.app"}
    m = out["com.gem.app"]
    assert m.name == "Gem" and m.seller == "Acme"
    assert m.release_date == "2026-06-03" and m.rating_count == 321 and m.avg_rating == 4.6


def test_empty_ids_no_runner_call():
    called = []
    assert lookup_google_play([], runner=lambda ids: called.append(ids) or "[]") == {}
    assert called == []


def test_error_entries_are_skipped():
    raw = json.dumps([{"appId": "com.ok", "title": "OK", "developer": "D", "genre": "G",
                       "releaseDateISO": "2026-01-01", "score": 4.0, "ratings": 5},
                      {"appId": "com.bad", "error": "not found"}])
    out = lookup_google_play(["com.ok", "com.bad"], runner=lambda ids: raw)
    assert set(out) == {"com.ok"}              # the error entry is dropped


def test_malformed_json_degrades_to_empty():
    assert lookup_google_play(["com.x"], runner=lambda ids: "<<not json>>") == {}
    assert lookup_google_play(["com.x"], runner=lambda ids: "") == {}


@pytest.mark.integration
def test_live_sidecar_runs_node():
    if not shutil.which("node") or not (Path(_SIDECAR).parent / "node_modules").exists():
        pytest.skip("node or sidecar node_modules not installed")
    out = lookup_google_play(["com.whatsapp"])
    assert "com.whatsapp" in out and out["com.whatsapp"].name
