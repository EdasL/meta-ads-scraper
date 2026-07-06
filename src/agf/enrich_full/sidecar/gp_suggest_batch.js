// Batched Google Play search autocomplete via facundoolano/google-play-scraper.
// One node process for a whole mint run (the per-term gp_suggest.js spawns one process
// per seed — ~120 spawns per run). Usage:
//   echo '["term a","term b"]' | node gp_suggest_batch.js
// Emits a JSON object {term: [suggestions]} to stdout; a per-term error degrades to []
// and an unparseable input to {} so the Python side never crashes.
import gp from 'google-play-scraper';
const g = gp.default || gp;

const CONCURRENCY = 8;

const chunks = [];
process.stdin.on('data', (c) => chunks.push(c));
process.stdin.on('end', async () => {
  let terms;
  try { terms = JSON.parse(Buffer.concat(chunks).toString('utf8')); }
  catch { process.stdout.write('{}'); return; }
  if (!Array.isArray(terms)) { process.stdout.write('{}'); return; }
  const out = {};
  for (let i = 0; i < terms.length; i += CONCURRENCY) {
    await Promise.all(terms.slice(i, i + CONCURRENCY).map(async (t) => {
      const term = String(t);
      try { out[term] = (await g.suggest({ term })) || []; }
      catch { out[term] = []; }
    }));
  }
  process.stdout.write(JSON.stringify(out));
});
