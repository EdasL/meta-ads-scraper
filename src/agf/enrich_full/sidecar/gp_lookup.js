// Android app-metadata sidecar (master-plan §6 P6-T3) via facundoolano/google-play-scraper.
// Usage: node gp_lookup.js <packageId> [<packageId> ...]
// Emits a JSON array to stdout. releaseDateISO normalizes `released` for the emerging gate.
async function main() {
  const mod = await import('google-play-scraper');
  const gplay = mod.default || mod;
  const ids = process.argv.slice(2);
  const out = [];
  for (const appId of ids) {
    try {
      const a = await gplay.app({ appId });
      let releaseDateISO = null;
      if (a.released) {
        const d = new Date(a.released);
        if (!isNaN(d.getTime())) releaseDateISO = d.toISOString().slice(0, 10);
      }
      out.push({ appId: a.appId, title: a.title, developer: a.developer,
                 genre: a.genre, released: a.released, releaseDateISO,
                 score: a.score, ratings: a.ratings });
    } catch (e) {
      out.push({ appId, error: String((e && e.message) || e) });
    }
  }
  process.stdout.write(JSON.stringify(out));
}
main().catch((e) => { process.stderr.write(String(e)); process.exit(1); });
