from dataclasses import dataclass, field


@dataclass
class AdRecord:
    ad_archive_id: str
    page_id: str
    page_name: str
    is_active: bool
    start_date: int | None
    end_date: int | None
    collation_count: int
    cta_type: str | None
    cta_text: str | None
    link_url: str | None
    body_text: str | None
    image_urls: list[str] = field(default_factory=list)
    video_urls: list[str] = field(default_factory=list)


def _connection(data: dict) -> dict:
    main = data.get("data", {}).get("ad_library_main") or data.get("data", {}).get(
        "adLibraryMain", {}
    )
    return main.get("search_results_connection") or main.get(
        "searchResultsConnection", {}
    )


def _to_record(node: dict) -> AdRecord:
    snap = node.get("snapshot") or {}
    body = snap.get("body") or {}
    return AdRecord(
        ad_archive_id=str(node.get("ad_archive_id", "")),
        page_id=str(node.get("page_id", "")),
        page_name=node.get("page_name", ""),
        is_active=bool(node.get("is_active")),
        start_date=node.get("start_date"),
        end_date=node.get("end_date"),
        collation_count=int(node.get("collation_count") or 0),
        cta_type=snap.get("cta_type"),
        cta_text=snap.get("cta_text"),
        link_url=snap.get("link_url"),
        body_text=body.get("text"),
        image_urls=[i["original_image_url"] for i in (snap.get("images") or []) if i.get("original_image_url")],
        video_urls=[v["video_hd_url"] for v in (snap.get("videos") or []) if v.get("video_hd_url")],
    )


def parse_search_response(data: dict) -> tuple[list[AdRecord], str | None, bool]:
    """Return (records, end_cursor, has_next_page)."""
    conn = _connection(data)
    records: list[AdRecord] = []
    for edge in conn.get("edges", []):
        for node in (edge.get("node", {}) or {}).get("collated_results", []):
            records.append(_to_record(node))
    page_info = conn.get("page_info") or conn.get("pageInfo") or {}
    cursor = page_info.get("end_cursor") or page_info.get("endCursor")
    has_next = bool(page_info.get("has_next_page") or page_info.get("hasNextPage"))
    return records, cursor, has_next
