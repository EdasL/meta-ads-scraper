"""EU→US bridge — re-query an EU-discovered page_id scoped to the US to pull its
richer US creative set, and emit app + page Candidates from those ads."""
from agf.classify import classify_url
from agf.discovery.seeds import Candidate


async def bridge_eu_to_us(extractor, page_id: str):
    """Pull a page's US ads via search_type='page' + view_all_page_id."""
    return await extractor.search(query="", countries=["US"], search_type="page",
                                  view_all_page_id=page_id)


async def run_bridge(extractor, page_ids) -> list[Candidate]:
    """For each EU page_id, bridge to US and emit a page Candidate plus an app
    Candidate for each ad whose link resolves to a store id."""
    cands: list[Candidate] = []
    for pid in page_ids:
        ads = await bridge_eu_to_us(extractor, pid)
        emitted_page = False
        for ad in ads:
            if not emitted_page:
                cands.append(Candidate(source="bridge", kind="page", page_id=ad.page_id,
                                       page_name=ad.page_name, ad_record=ad))
                emitted_page = True
            intent = classify_url(ad.link_url)
            if intent.kind in ("ios", "android") and intent.store_id:
                cands.append(Candidate(
                    source="bridge", kind="app",
                    store="ios" if intent.kind == "ios" else "android",
                    store_id=intent.store_id, page_id=ad.page_id, page_name=ad.page_name,
                    app_meta={"store": "ios" if intent.kind == "ios" else "android",
                              "store_id": intent.store_id},
                    ad_record=ad))
    return cands
