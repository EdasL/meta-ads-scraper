"""Funnel handling (master-plan §6 P6-T4/T5/T6/T7): resolve the landing URL (via the
Plan 2 resolver — no second resolver), dismiss CMP banners, screenshot the funnel
step-by-step → S3, and best-effort pricing extraction. Playwright is the optional
standby dep (lazy-imported)."""
from agf.enrich_full.schemas import ResolvedTarget


async def resolve_landing(extractor, url, ua="ios") -> ResolvedTarget:
    """Resolve an ad's destination via the Plan 2 Extractor.resolve_deeplink (§3.4).
    Returns a ResolvedTarget carrying both the original landing URL and the final URL."""
    resolved = await extractor.resolve_deeplink(url, ua=ua)
    return ResolvedTarget(kind=resolved.intent.kind, store_id=resolved.intent.store_id,
                          landing_url=url, final_url=resolved.final_url)
