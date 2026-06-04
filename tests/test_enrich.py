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
        rating_count=340, avg_rating=4.6,
    )


def test_parse_lookup_empty_returns_none():
    assert parse_lookup({"resultCount": 0, "results": []}) is None


def test_emerging_true_when_recent():
    assert emerging_from_release("2026-02-10T08:00:00Z", NOW, months=18) is True


def test_emerging_false_when_old():
    assert emerging_from_release("2022-01-01T00:00:00Z", NOW, months=18) is False
