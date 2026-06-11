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
    page_like_count: int | None = None   # snapshot.page_like_count — cheap incumbent-size signal
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
        page_like_count=snap.get("page_like_count"),
        image_urls=_image_urls(snap),
        video_urls=_video_urls(snap),
    )


def _image_urls(snap: dict) -> list[str]:
    """original first, resized as fallback; carousel ads keep media in
    snapshot.cards (live-probed 2026-06-11 — images/videos arrays empty there)."""
    urls = [i.get("original_image_url") or i.get("resized_image_url")
            for i in (snap.get("images") or [])]
    for c in (snap.get("cards") or []):
        if c.get("video_hd_url") or c.get("video_sd_url"):
            continue                          # video card — counted in _video_urls
        urls.append(c.get("original_image_url") or c.get("resized_image_url"))
    return [u for u in urls if u]


def _video_urls(snap: dict) -> list[str]:
    """HD preferred, SD is common alone (11/60 ads in one live listing)."""
    urls = [v.get("video_hd_url") or v.get("video_sd_url")
            for v in (snap.get("videos") or [])]
    urls += [c.get("video_hd_url") or c.get("video_sd_url")
             for c in (snap.get("cards") or [])]
    return [u for u in urls if u]


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
