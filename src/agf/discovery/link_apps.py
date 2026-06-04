"""Link an app candidate to its Meta advertiser: search Meta for the app, resolve
each ad's deeplink, and on a store_id match wire ads.app_id + advertiser_id (§3.3),
returning the page_id. Closes the app↔advertiser gap (master-plan §7 #5)."""
from agf.db import repo


async def link_app_to_advertiser(conn, extractor, candidate) -> str | None:
    """Return the linked page_id, or None if no Meta ad resolves to this app."""
    query = (candidate.app_meta or {}).get("name") or candidate.store_id or ""
    ads = await extractor.search(query=query, countries=["US"])
    for ad in ads:
        resolved = await extractor.resolve_deeplink(ad.link_url)
        if resolved.intent.store_id and resolved.intent.store_id == candidate.store_id:
            advertiser_id = repo.upsert_advertiser(conn, ad)
            app_id = repo.upsert_app(
                conn, candidate.app_meta or {"store": candidate.store, "store_id": candidate.store_id})
            repo.upsert_ad(conn, ad, advertiser_id, app_id)
            return ad.page_id
    return None
