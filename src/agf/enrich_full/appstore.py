"""App-store metadata enrichment (master-plan §6 P6-T2/T3).

iTunes Lookup (batch) reuses Plan 1 parse_lookup; Google Play goes through a Node
sidecar. RESEARCH RULE: use releaseDate (first release) for the emerging gate, NOT
currentVersionReleaseDate (last update)."""
import json
import subprocess
from pathlib import Path
from agf.enrich import parse_lookup, AppMeta

_ITUNES_LOOKUP = "https://itunes.apple.com/lookup"
_SIDECAR = Path(__file__).parent / "sidecar" / "gp_lookup.js"


def _chunks(seq, n):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def lookup_itunes_batch(client, ids, *, chunk=200, country="us") -> dict:
    """Look up iOS apps by trackId in ≤chunk batches -> {track_id: AppMeta}.
    Reuses Plan 1 parse_lookup per result (single source of releaseDate truth)."""
    out: dict = {}
    for batch in _chunks([str(i) for i in ids if i], chunk):
        resp = client.get(_ITUNES_LOOKUP, params={
            "id": ",".join(batch), "entity": "software", "country": country})
        payload = resp.json() or {}
        for r in payload.get("results", []):
            meta = parse_lookup({"results": [r]})
            if meta and meta.track_id:
                out[meta.track_id] = meta
    return out


def _default_node_runner(ids):
    return subprocess.run(["node", str(_SIDECAR), *ids],
                          capture_output=True, text=True, timeout=90).stdout


def lookup_google_play(package_ids, *, runner=None) -> dict:
    """Look up Android apps via the Node google-play-scraper sidecar -> {package_id:
    AppMeta}. `runner(ids)->stdout` is injectable for tests. release_date uses the
    sidecar's normalized ISO date (first release)."""
    ids = [str(p) for p in package_ids if p]
    if not ids:
        return {}
    runner = runner or _default_node_runner
    raw = runner(ids) or "[]"
    out: dict = {}
    for r in json.loads(raw):
        track_id = str(r.get("appId", ""))
        if not track_id or r.get("error"):
            continue
        out[track_id] = AppMeta(
            track_id=track_id, name=r.get("title", ""), seller=r.get("developer", ""),
            genre=r.get("genre", ""),
            release_date=r.get("releaseDateISO") or r.get("released") or "",
            rating_count=int(r.get("ratings") or 0),
            avg_rating=float(r.get("score") or 0.0))
    return out
