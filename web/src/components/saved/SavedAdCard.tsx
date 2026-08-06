"use client";

import { useState } from "react";
import Link from "next/link";
import { IconDownload, IconHeartFilled } from "@tabler/icons-react";
import { unsaveAd } from "@/lib/useSavedAds";
import { rememberOrigin } from "@/lib/returnTo";
import { adDownloadName, adMediaUrl as mediaUrl } from "@/lib/adMedia";
import type { ApiSavedAd } from "@/types/contracts";

const downloadName = (ad: ApiSavedAd) =>
  adDownloadName({
    pageName: ad.page_name,
    appName: ad.app_name,
    externalAdId: ad.external_ad_id,
    mediaKey: ad.media_s3_key,
    mediaType: ad.media_type,
  });

// Natural aspect ratio — reels are 9:16; a fixed landscape crop hides most of the ad.
const mediaStyle: React.CSSProperties = {
  width: "100%",
  height: "auto",
  display: "block",
  borderTop: "1px solid var(--line)",
  borderBottom: "1px solid var(--line)",
};

/** One saved ad in the /saved → Ads grid: the page it ran under (linking back
 * to that gem's dossier), the creative, and the ad copy. The filled heart
 * unsaves. Mirrors the dossier's AdCard rendering. */
export function SavedAdCard({ ad }: { ad: ApiSavedAd }) {
  const [broken, setBroken] = useState(false);
  const src = ad.media_s3_key ? mediaUrl(ad.media_s3_key) : undefined;
  const hasMedia = !!src && !broken;
  const label = ad.page_name ?? ad.app_name ?? "Unknown page";
  const nameStyle: React.CSSProperties = {
    minWidth: 0,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
  return (
    <div
      style={{
        border: "1px solid var(--line)",
        background: "var(--paper)",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, padding: "8px 10px" }}>
        {ad.advertiser_id ? (
          <Link href={`/gems/${ad.advertiser_id}`} onClick={rememberOrigin} className="t-sm w-strong" style={nameStyle}>
            {label}
          </Link>
        ) : (
          <span className="t-sm w-strong" style={nameStyle}>{label}</span>
        )}
        <button
          type="button"
          onClick={() => unsaveAd(ad.external_ad_id)}
          aria-label="Remove from saved"
          title="Remove from saved"
          style={{
            background: "none",
            border: "none",
            padding: 2,
            cursor: "pointer",
            display: "inline-flex",
            color: "var(--ink)",
            flexShrink: 0,
          }}
        >
          <IconHeartFilled size={16} aria-hidden />
        </button>
      </div>
      {hasMedia &&
        (ad.media_type === "video" ? (
          <video src={src} controls preload="metadata" playsInline onError={() => setBroken(true)} style={mediaStyle} />
        ) : (
          // eslint-disable-next-line @next/next/no-img-element -- dynamic /backend/media proxy thumbnails; next/image is a poor fit for an internal tool
          <img
            src={src}
            alt={ad.title ?? ad.body_text ?? "ad creative"}
            loading="lazy"
            onError={() => setBroken(true)}
            style={mediaStyle}
          />
        ))}
      {ad.body_text && (
        <div
          style={{
            fontSize: "var(--fs-xs)",
            color: "var(--ink-soft)",
            padding: "8px 10px",
            lineHeight: 1.45,
            display: "-webkit-box",
            WebkitLineClamp: 3,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          {ad.body_text}
        </div>
      )}
      {!hasMedia && !ad.body_text && (
        <div style={{ fontSize: "var(--fs-xs)", color: "var(--muted)", padding: "8px 10px" }}>
          {ad.title ?? "Creative not available for this ad."}
        </div>
      )}
      {hasMedia && src && (
        // The media proxy is same-origin, so `download` saves the file straight
        // to disk instead of opening it in a tab.
        <div style={{ marginTop: "auto", padding: "8px 10px", borderTop: "1px solid var(--line)" }}>
          <a
            href={src}
            download={downloadName(ad)}
            className="font-mono"
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              fontSize: "0.62rem",
              padding: "5px 10px",
              border: "1px solid var(--ink)",
              color: "var(--ink)",
              textDecoration: "none",
              whiteSpace: "nowrap",
            }}
          >
            <IconDownload size={13} aria-hidden />
            {ad.media_type === "video" ? "Download video" : "Download image"}
          </a>
        </div>
      )}
    </div>
  );
}
