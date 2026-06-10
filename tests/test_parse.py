import json
from pathlib import Path
from agf.parse import parse_search_response, AdRecord

DATA = json.loads(
    (Path(__file__).parent / "fixtures" / "search_response.json").read_text()
)


def test_parse_returns_records_and_cursor():
    records, cursor, has_next = parse_search_response(DATA)
    assert cursor == "CUR_NEXT"
    assert has_next is True
    assert len(records) == 1


def test_record_fields():
    rec = parse_search_response(DATA)[0][0]
    assert isinstance(rec, AdRecord)
    assert rec.ad_archive_id == "111"
    assert rec.page_id == "P1"
    assert rec.page_name == "FitGem"
    assert rec.is_active is True
    assert rec.cta_type == "INSTALL_MOBILE_APP"
    assert rec.link_url == "https://apps.apple.com/us/app/fitgem/id6470000001"
    assert rec.collation_count == 7
    assert rec.page_like_count == 842        # snapshot.page_like_count — incumbent-size signal
    assert rec.start_date == 1714500000
    assert rec.image_urls == ["https://scontent.fbcdn.net/x.jpg"]
    assert rec.video_urls == ["https://video.fbcdn.net/y.mp4"]


def test_parse_empty_response_is_safe():
    records, cursor, has_next = parse_search_response({"data": {}})
    assert records == [] and cursor is None and has_next is False
