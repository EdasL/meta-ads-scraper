// Google Play search autocomplete via facundoolano/google-play-scraper. The breadth
// channel that mines what real users type into Play search. Usage:
//   node gp_suggest.js "<term>"
// Emits a JSON array of suggestion strings to stdout; any error degrades to [] so the
// Python side returns an empty list instead of crashing.
import gp from 'google-play-scraper';
const g = gp.default || gp;
const term = process.argv[2] || '';
g.suggest({ term })
  .then((r) => { process.stdout.write(JSON.stringify(r || [])); })
  .catch(() => { process.stdout.write('[]'); });
