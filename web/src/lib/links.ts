/**
 * Pure link-building for the gem detail page — no React, no fetch. Turns the
 * dossier's identifiers (Facebook page id, domain, app-store listing) into real
 * external URLs the founder can click. Unit-tested in `links.test.ts` (run with
 * `node --test`), which is why this lives in a plain `.ts` file, not the `.tsx`
 * component.
 */
import type { GemDossier } from "@/types/contracts";

export type LinkChip = { label: string; href: string };

/** Strip any scheme + trailing slashes from a domain, e.g. "https://x.com/" → "x.com". */
export function bareDomain(domain: string | null | undefined): string {
  if (!domain) return "";
  return domain.replace(/^https?:\/\//i, "").replace(/\/+$/, "");
}

/**
 * Build the link set from the dossier. Order is stable (profile, website, app
 * store, ads library); links whose data is missing are simply omitted.
 */
export function buildGemLinks(gem: GemDossier): LinkChip[] {
  const links: LinkChip[] = [];
  const pageId = gem.company.page_id;
  const domain = bareDomain(gem.company.domain);
  const app = gem.apps[0];

  // Profile → the Facebook page itself.
  if (pageId) {
    links.push({ label: "profile", href: `https://www.facebook.com/${pageId}` });
  }

  // Website → normalized https URL.
  if (domain) {
    links.push({ label: "website", href: `https://${domain}` });
  }

  // App Store / Play Store → from the first joined app listing.
  if (app) {
    if (app.store === "ios") {
      // store_id may already be "id1693820"; otherwise prefix "id".
      const id = /^id/i.test(app.store_id) ? app.store_id : `id${app.store_id}`;
      links.push({ label: "app store", href: `https://apps.apple.com/app/${id}` });
    } else {
      links.push({
        label: "play store",
        href: `https://play.google.com/store/apps/details?id=${app.store_id}`,
      });
    }
  }

  // Ads Library → every active ad this page runs.
  if (pageId) {
    links.push({
      label: "ads library",
      href: `https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=US&view_all_page_id=${pageId}`,
    });
  }

  return links;
}
