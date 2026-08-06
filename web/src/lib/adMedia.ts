/** Ad creative media: the same-origin URL and a human download filename.
 *
 * Media stays same-origin (`/backend/*`) so browser <img>/<video> tags carry
 * the session cookie through the proxy — the api host requires auth a bare tag
 * cannot send. The same property is what makes `<a download>` save the file
 * instead of navigating to it.
 */
export const adMediaUrl = (key: string) => `/backend/media/${key}`;

interface DownloadNameInput {
  pageName?: string | null;
  appName?: string | null;
  externalAdId: string;
  mediaKey?: string | null;
  mediaType?: string | null;
}

/** Creative keys are content-addressed (`creatives/<sha256>.jpg`), so the raw
 * file would land in Downloads under a 64-char hash. Name it after the ad
 * instead: "bepresent-1271851644912798.jpg". Mirrored server-side in
 * backend/controllers/saved_ads_controller.go for the bulk zip — keep the two
 * in step so a single download and a zipped one agree. */
export function adDownloadName(ad: DownloadNameInput): string {
  const ext = ad.mediaKey?.split(".").pop()?.toLowerCase();
  const safeExt = ext && /^[a-z0-9]{2,4}$/.test(ext) ? ext : ad.mediaType === "video" ? "mp4" : "jpg";
  const slug = (ad.pageName ?? ad.appName ?? "ad")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
  return `${slug || "ad"}-${ad.externalAdId}.${safeExt}`;
}
