// Android recent-reviews sidecar via facundoolano/google-play-scraper.
// Usage: node gp_reviews.js <packageId> [num]
// Emits a JSON array of {id, userName, score, title, text, date} (NEWEST first) to
// stdout. A lookup failure emits [{"appId": ..., "error": ...}] so the caller can
// degrade instead of crashing (emerging apps may simply have no reviews yet).
async function main() {
  const mod = await import('google-play-scraper');
  const gplay = mod.default || mod;
  const appId = process.argv[2];
  const num = parseInt(process.argv[3] || '20', 10);
  if (!appId) {
    process.stdout.write('[]');
    return;
  }
  try {
    const res = await gplay.reviews({ appId, sort: gplay.sort.NEWEST, num });
    const rows = (res && res.data) || [];
    process.stdout.write(JSON.stringify(rows.map((r) => ({
      id: r.id, userName: r.userName, score: r.score, title: r.title,
      text: r.text, date: r.date,
    }))));
  } catch (e) {
    process.stdout.write(JSON.stringify([{ appId, error: String((e && e.message) || e) }]));
  }
}
main().catch((e) => { process.stderr.write(String(e)); process.exit(1); });
