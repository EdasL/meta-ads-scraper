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


def test_parses_preview_and_install_fields():
    def fake_runner(ids):
        return json.dumps([
            {"appId": "com.gem.app", "title": "Gem", "developer": "Acme",
             "genre": "Health", "releaseDateISO": "2026-06-03", "score": 4.6,
             "ratings": 321, "icon": "https://play/icon.png",
             "screenshots": ["https://play/s1.png", "https://play/s2.png"],
             "maxInstalls": 116000000, "installs": "100,000,000+",
             "priceText": "Free"},
        ])
    m = lookup_google_play(["com.gem.app"], runner=fake_runner)["com.gem.app"]
    assert m.icon_url == "https://play/icon.png"
    assert m.screenshots == ["https://play/s1.png", "https://play/s2.png"]
    assert m.installs == 116000000             # maxInstalls (near-exact), not the bucket
    assert m.price_label == "Free"


def test_missing_preview_fields_default_to_none():
    raw = json.dumps([{"appId": "com.min", "title": "Min", "developer": "D",
                       "genre": "G", "releaseDateISO": "2026-01-01",
                       "score": 4.0, "ratings": 5}])
    m = lookup_google_play(["com.min"], runner=lambda ids: raw)["com.min"]
    assert m.icon_url is None and m.screenshots is None
    assert m.installs is None and m.price_label is None


def test_genre_id_maps_to_is_game():
    raw = json.dumps([
        {"appId": "com.game", "title": "G", "developer": "D", "genre": "Puzzle",
         "genreId": "GAME_PUZZLE", "releaseDateISO": "2026-01-01", "score": 4.0, "ratings": 5},
        {"appId": "com.fit", "title": "F", "developer": "D", "genre": "Health & Fitness",
         "genreId": "HEALTH_AND_FITNESS", "releaseDateISO": "2026-01-01", "score": 4.0,
         "ratings": 5}])
    out = lookup_google_play(["com.game", "com.fit"], runner=lambda ids: raw)
    assert out["com.game"].is_game is True     # genreId GAME_* is the authoritative flag
    assert out["com.fit"].is_game is False


def test_missing_genre_id_leaves_is_game_none():
    raw = json.dumps([{"appId": "com.min", "title": "Min", "developer": "D",
                       "genre": "Arcade", "releaseDateISO": "2026-01-01",
                       "score": 4.0, "ratings": 5}])
    m = lookup_google_play(["com.min"], runner=lambda ids: raw)["com.min"]
    assert m.is_game is None                   # display-name genre is NEVER used


def test_sidecar_emits_genre_id():
    # contract guard: the Node sidecar must surface google-play-scraper's genreId,
    # the only authoritative android game signal.
    assert "genreId" in Path(_SIDECAR).read_text()


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


def test_batches_large_id_lists_so_no_subprocess_is_oversized():
    """The Node sidecar fetches each app SEQUENTIALLY, so one subprocess over a large
    backlog blows past its 90s timeout and strands the whole Android triage group
    (2026-06-29 wedge: 1,434 candidates stuck at 'new'). lookup must split ids into
    small batches so each subprocess stays well under the timeout — and still merge
    the results across batches."""
    calls = []

    def runner(ids):
        calls.append(len(ids))
        return json.dumps([{"appId": i, "title": "A", "developer": "D", "genre": "G",
                            "releaseDateISO": "2026-01-01", "score": 4.0, "ratings": 1}
                           for i in ids])

    ids = [f"com.app{n}" for n in range(120)]
    out = lookup_google_play(ids, runner=runner)
    assert len(out) == 120                      # every id resolved, merged across batches
    assert calls and max(calls) <= 50           # no single subprocess gets the whole backlog


def test_crashed_batch_degrades_to_empty_so_triage_strands_and_retries():
    """A non-JSON (crashed) batch must collapse the whole lookup to {} — triage treats
    an empty Android result as a transient failure and re-queues the group, so a sidecar
    blip never silently drops apps as 'no_us_listing'."""
    def runner(ids):
        return "<<crash>>" if "com.app99" in ids else json.dumps(
            [{"appId": i, "title": "A", "developer": "D", "genre": "G",
              "releaseDateISO": "2026-01-01", "score": 4.0, "ratings": 1} for i in ids])

    out = lookup_google_play([f"com.app{n}" for n in range(120)], runner=runner)
    assert out == {}


@pytest.mark.integration
def test_live_sidecar_runs_node():
    if not shutil.which("node") or not (Path(_SIDECAR).parent / "node_modules").exists():
        pytest.skip("node or sidecar node_modules not installed")
    out = lookup_google_play(["com.whatsapp"])
    assert "com.whatsapp" in out and out["com.whatsapp"].name
