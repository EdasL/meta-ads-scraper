import type { GemDossier } from "@/types/contracts";
import { adLibraryPageUrl } from "@/lib/links";
import { monthLabel } from "@/lib/why";

// Lists every Meta page running ads for this app, across both stores. Running
// one app from many pages is a common ad technique (persona pages, geographic
// splits, keeping volume off the main account), so this is informational — it
// shows the whole spread behind one gem, not a quality score.
export function PagesSection({ gem }: { gem: GemDossier }) {
  const pages = gem.pages ?? [];
  if (pages.length <= 1) return null;

  return (
    <section>
      <h2 className="t-sm w-strong" style={{ marginBottom: 10 }}>
        Pages running this app ({pages.length})
      </h2>
      <table className="gem-table">
        <thead>
          <tr>
            <th>Page</th>
            <th className="num">Ads (active / total)</th>
            <th>Running</th>
            <th aria-label="Ad Library" />
          </tr>
        </thead>
        <tbody>
          {pages.map((p) => (
            <tr key={p.advertiser_id}>
              <td>
                <span className="w-strong">{p.page_name ?? "Unnamed page"}</span>
                {p.page_category && (
                  <span className="t-xs" style={{ color: "var(--muted)", marginLeft: 8 }}>
                    {p.page_category}
                  </span>
                )}
              </td>
              <td className="num nums">
                {p.active_ads} / {p.total_ads}
              </td>
              <td className="t-xs nums" style={{ whiteSpace: "nowrap" }}>
                {monthLabel(p.first_ad) ?? "—"} – {monthLabel(p.last_ad) ?? "—"}
              </td>
              <td>
                {p.page_id && (
                  <a className="t-xs" href={adLibraryPageUrl(p.page_id)} target="_blank" rel="noreferrer">
                    Ad Library
                  </a>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
