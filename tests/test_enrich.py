import json
from datetime import datetime, timezone
from pathlib import Path
from agf.enrich import parse_lookup, AppMeta, emerging_from_release

LOOKUP = json.loads(
    (Path(__file__).parent / "fixtures" / "itunes_lookup.json").read_text()
)
NOW = datetime(2026, 6, 4, tzinfo=timezone.utc)


def test_parse_lookup():
    meta = parse_lookup(LOOKUP)
    assert meta == AppMeta(
        track_id="6470000001", name="FitGem", seller="Gem Labs Inc",
        genre="Health & Fitness", release_date="2026-02-10T08:00:00Z",
        rating_count=340, avg_rating=4.6, price_label="Free",
    )


def test_parse_lookup_empty_returns_none():
    assert parse_lookup({"resultCount": 0, "results": []}) is None


def test_parse_lookup_maps_preview_fields():
    payload = {"results": [{
        "trackId": 99, "trackName": "X", "artworkUrl512": "https://a/512.png",
        "screenshotUrls": ["https://s/1.png", "https://s/2.png"],
        "ipadScreenshotUrls": ["https://ipad/1.png"], "formattedPrice": "$2.99"}]}
    meta = parse_lookup(payload)
    assert meta.icon_url == "https://a/512.png"
    assert meta.screenshots == ["https://s/1.png", "https://s/2.png"]  # iPhone first
    assert meta.price_label == "$2.99"
    assert meta.installs is None                  # Apple publishes no install counts


def test_parse_lookup_falls_back_to_ipad_screenshots():
    # screenshotUrls CAN be empty while ipadScreenshotUrls is populated (live-probed)
    payload = {"results": [{"trackId": 99, "trackName": "X", "screenshotUrls": [],
                            "ipadScreenshotUrls": ["https://ipad/1.png"]}]}
    meta = parse_lookup(payload)
    assert meta.screenshots == ["https://ipad/1.png"]


def test_parse_lookup_no_screenshots_is_none():
    meta = parse_lookup({"results": [{"trackId": 1, "trackName": "X",
                                      "screenshotUrls": [], "ipadScreenshotUrls": []}]})
    assert meta.screenshots is None


def test_emerging_true_when_recent():
    assert emerging_from_release("2026-02-10T08:00:00Z", NOW, months=18) is True


def test_emerging_false_when_old():
    assert emerging_from_release("2022-01-01T00:00:00Z", NOW, months=18) is False
