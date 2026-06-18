// Android app-GRAPH sidecar via facundoolano/google-play-scraper: similar apps +
// same-developer apps — the breadth channel that jumps vocabulary basins keyword
// mining can't reach. Usage:
//   node gp_graph.js similar   <appId>
//   node gp_graph.js developer <devId>
// Emits a JSON array of {appId, title, developer, genre} to stdout. Errors degrade to
// a single {error} element so the Python side returns [] instead of crashing.
async function main() {
  const mod = await import('google-play-scraper');
  const gplay = mod.default || mod;
  const [cmd, arg] = process.argv.slice(2);
  let rows = [];
  try {
    if (cmd === 'similar') rows = await gplay.similar({ appId: arg });
    else if (cmd === 'developer') rows = await gplay.developer({ devId: arg });
    else throw new Error('usage: gp_graph.js similar|developer <id>');
  } catch (e) {
    process.stdout.write(JSON.stringify([{ error: String((e && e.message) || e) }]));
    return;
  }
  const out = rows.map((a) => ({
    appId: a.appId,
    title: a.title,
    developer: (a.developer && a.developer.devId) || a.developer,
    genre: a.genre,
  }));
  process.stdout.write(JSON.stringify(out));
}
main().catch((e) => { process.stderr.write(String(e)); process.exit(1); });
