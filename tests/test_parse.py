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


def _node_with_snapshot(snap):
    return {"data": {"ad_library_main": {"search_results_connection": {
        "edges": [{"node": {"collated_results": [
            {"ad_archive_id": "999", "page_id": "P9", "snapshot": snap}]}}],
        "page_info": {"end_cursor": None, "has_next_page": False}}}}}


def test_sd_only_video_is_extracted():
    # Live-probed 2026-06-11: 11/60 ads in one listing carry ONLY video_sd_url.
    rec = parse_search_response(_node_with_snapshot(
        {"videos": [{"video_sd_url": "https://video.fbcdn.net/sd.mp4"}]}))[0][0]
    assert rec.video_urls == ["https://video.fbcdn.net/sd.mp4"]


def test_hd_video_preferred_over_sd():
    rec = parse_search_response(_node_with_snapshot(
        {"videos": [{"video_hd_url": "https://video.fbcdn.net/hd.mp4",
                     "video_sd_url": "https://video.fbcdn.net/sd.mp4"}]}))[0][0]
    assert rec.video_urls == ["https://video.fbcdn.net/hd.mp4"]


def test_resized_image_fallback():
    rec = parse_search_response(_node_with_snapshot(
        {"images": [{"resized_image_url": "https://scontent.fbcdn.net/r.jpg"}]}))[0][0]
    assert rec.image_urls == ["https://scontent.fbcdn.net/r.jpg"]


def test_carousel_cards_media_is_extracted():
    # Carousel ads keep media in snapshot.cards (images/videos arrays empty).
    rec = parse_search_response(_node_with_snapshot(
        {"cards": [
            {"original_image_url": "https://scontent.fbcdn.net/c1.jpg"},
            {"video_hd_url": "https://video.fbcdn.net/c2.mp4",
             "video_preview_image_url": "https://scontent.fbcdn.net/c2p.jpg"},
            {"resized_image_url": "https://scontent.fbcdn.net/c3.jpg"},
        ]}))[0][0]
    assert rec.image_urls == ["https://scontent.fbcdn.net/c1.jpg",
                              "https://scontent.fbcdn.net/c3.jpg"]
    assert rec.video_urls == ["https://video.fbcdn.net/c2.mp4"]
