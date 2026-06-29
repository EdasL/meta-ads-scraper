"""App-store metadata enrichment (master-plan §6 P6-T2/T3).

iTunes Lookup (batch) reuses Plan 1 parse_lookup; Google Play goes through a Node
sidecar. RESEARCH RULE: use releaseDate (first release) for the emerging gate, NOT
currentVersionReleaseDate (last update)."""
import json
import subprocess
import time as _time
from pathlib import Path
from sqlalchemy import text
from agf.enrich import parse_lookup, AppMeta
from agf.db.repo import _cap_description

_ITUNES_LOOKUP = "https://itunes.apple.com/lookup"
_ITUNES_NEW_APPS = "https://itunes.apple.com/{country}/rss/{feed}/limit={limit}{genre}/json"
_SIDECAR = Path(__file__).parent / "sidecar" / "gp_lookup.js"


def _rss_dig(node, *keys):
    """Walk nested dicts; None as soon as a step is missing (RSS JSON is `.label`-heavy)."""
    cur = node
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def parse_itunes_new_releases(payload) -> list[dict]:
    """Parse an iTunes new-applications RSS JSON feed into {store_id, name, genre} rows.
    A single-entry feed arrives as a dict (not a list); an empty/missing feed yields []."""
    entries = _rss_dig(payload or {}, "feed", "entry") or []
    if isinstance(entries, dict):
        entries = [entries]
    out: list[dict] = []
    for e in entries:
        store_id = _rss_dig(e, "id", "attributes", "im:id")
        name = _rss_dig(e, "im:name", "label")
        if not store_id or not name:
            continue
        out.append({"store_id": str(store_id), "name": str(name),
                    "genre": _rss_dig(e, "category", "attributes", "label")})
    return out


def fetch_itunes_new_releases(client, *, country="us", genre=None, limit=100,
                              free_only=False) -> list[dict]:
    """Fetch iTunes new-release apps (optionally scoped to one genre id) ->
    [{store_id, name, genre}]. New apps carry userRatingCount=0 on launch day, so this is
    an emerging-by-construction discovery source (Apple RSS — zero Meta traffic). A bad
    feed / non-JSON body degrades to []."""
    feed = "newfreeapplications" if free_only else "newapplications"
    genre_seg = f"/genre={genre}" if genre else ""
    url = _ITUNES_NEW_APPS.format(country=country, feed=feed, limit=limit, genre=genre_seg)
    try:
        payload = client.get(url).json() or {}
    except (ValueError, TypeError):
        return []
    return parse_itunes_new_releases(payload)


def _chunks(seq, n):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# --- is_game classification (authoritative store taxonomies ONLY; never inferred
# --- from genre display names — those are localized, free-form strings) ---------
_IOS_GAMES_GENRE_ID = "6014"


def _ios_is_game(result: dict):
    """iOS game flag from a raw iTunes Lookup result. iTunes returns genreIds as
    strings, but handle ints too. None when the result has no genre info at all."""
    primary = result.get("primaryGenreName")
    genre_ids = result.get("genreIds")
    if primary is None and genre_ids is None:
        return None
    ids = {str(g) for g in (genre_ids or [])}
    return primary == "Games" or _IOS_GAMES_GENRE_ID in ids


def _android_is_game(genre_id):
    """Android game flag from google-play-scraper's genreId (e.g. GAME_PUZZLE).
    None when the sidecar did not emit one."""
    if not genre_id:
        return None
    return str(genre_id).startswith("GAME_")


def lookup_itunes_batch(client, ids, *, chunk=200, country="us",
                        pause=0.0, sleep=_time.sleep) -> dict:
    """Look up iOS apps by trackId in ≤chunk batches -> {track_id: AppMeta}.
    Reuses Plan 1 parse_lookup per result (single source of releaseDate truth).
    `pause` seconds between batch calls keeps bulk refreshes under the ~20 calls/min
    Lookup limit (never before the first call)."""
    out: dict = {}
    for i, batch in enumerate(_chunks([str(i) for i in ids if i], chunk)):
        if i > 0 and pause:
            sleep(pause)
        resp = client.get(_ITUNES_LOOKUP, params={
            "id": ",".join(batch), "entity": "software", "country": country})
        payload = resp.json() or {}
        for r in payload.get("results", []):
            meta = parse_lookup({"results": [r]})
            if meta and meta.track_id:
                meta.is_game = _ios_is_game(r)
                out[meta.track_id] = meta
    return out


def _default_node_runner(ids):
    return subprocess.run(["node", str(_SIDECAR), *ids],
                          capture_output=True, text=True, timeout=90).stdout


def lookup_google_play(package_ids, *, runner=None, chunk=50) -> dict:
    """Look up Android apps via the Node google-play-scraper sidecar -> {package_id:
    AppMeta}. `runner(ids)->stdout` is injectable for tests. release_date uses the
    sidecar's normalized ISO date (first release).

    Ids are looked up in ≤chunk batches: the sidecar fetches each app SEQUENTIALLY, so a
    single subprocess over a large backlog blows past its 90s timeout and strands the whole
    Android triage group (2026-06-29 wedge). A crashed/non-JSON batch collapses the result
    to {} so the caller (triage, empty_is_failure) re-queues the group rather than dropping
    apps as 'no_us_listing' — never return a partial dict."""
    ids = [str(p) for p in package_ids if p]
    if not ids:
        return {}
    runner = runner or _default_node_runner
    out: dict = {}
    for batch in _chunks(ids, chunk):
        raw = runner(batch) or "[]"
        try:
            rows = json.loads(raw)
        except (ValueError, TypeError):
            return {}                   # sidecar crashed / emitted non-JSON → degrade
        for r in rows:
            track_id = str(r.get("appId", ""))
            if not track_id or r.get("error"):
                continue
            max_installs = r.get("maxInstalls")
            meta = AppMeta(
                track_id=track_id, name=r.get("title", ""), seller=r.get("developer", ""),
                genre=r.get("genre", ""),
                release_date=r.get("releaseDateISO") or r.get("released") or "",
                rating_count=int(r.get("ratings") or 0),
                avg_rating=float(r.get("score") or 0.0),
                description=r.get("description", ""),
                icon_url=r.get("icon"),
                screenshots=(r.get("screenshots") or None),
                installs=int(max_installs) if max_installs is not None else None,
                price_label=r.get("priceText"))
            meta.is_game = _android_is_game(r.get("genreId"))
            out[track_id] = meta
    return out


_GRAPH_SIDECAR = Path(__file__).parent / "sidecar" / "gp_graph.js"


def _default_graph_runner(cmd, arg):
    return subprocess.run(["node", str(_GRAPH_SIDECAR), cmd, str(arg)],
                          capture_output=True, text=True, timeout=90).stdout


def _gplay_graph(cmd, arg, *, runner=None) -> list[dict]:
    """Run the app-graph sidecar and normalize to the new-releases discovery shape
    [{store_id, name, genre}] so graph results feed the SAME typeahead expander as
    new-releases. `runner(cmd, arg) -> stdout` is injectable for tests; a crashed
    sidecar / non-JSON / error rows degrade to []."""
    runner = runner or _default_graph_runner
    raw = runner(cmd, arg) or "[]"
    try:
        rows = json.loads(raw)
    except (ValueError, TypeError):
        return []
    out: list[dict] = []
    for r in rows:
        if not isinstance(r, dict) or r.get("error") or not r.get("appId"):
            continue
        out.append({"store_id": str(r["appId"]), "name": r.get("title") or "",
                    "genre": r.get("genre")})
    return out


def gplay_similar(package_id, *, runner=None) -> list[dict]:
    """Google Play 'similar apps' for a package id — the app-graph breadth channel that
    jumps vocabulary basins keyword mining can't reach. -> [{store_id, name, genre}]."""
    return _gplay_graph("similar", package_id, runner=runner)


def gplay_developer(dev_id, *, runner=None) -> list[dict]:
    """Same-developer apps for a developer id/name (a winning dev often ships more
    winners). -> [{store_id, name, genre}]."""
    return _gplay_graph("developer", dev_id, runner=runner)


def resolve_pending_app_metadata(conn, itunes_client, *, gplay_runner=None) -> int:
    """Fill name/genre/release/ratings for linked apps still missing a name (iTunes batch +
    gplay sidecar). Cheap — no Meta traffic — so it can run every sweep: it makes discovered
    apps interpretable AND feeds genre (cohort), release_date (emerging gate), and
    rating_count (under_radar) into scoring for ALL advertisers, not just enriched gems.
    conn-first: no commit. Idempotent (only touches name-NULL rows). Returns rows updated."""
    rows = conn.execute(text(
        "SELECT id, store, store_id FROM public.apps WHERE name IS NULL")).all()
    if not rows:
        return 0
    ios = [sid for _, store, sid in rows if store == "ios"]
    android = [sid for _, store, sid in rows if store == "android"]
    itunes = lookup_itunes_batch(itunes_client, ios) if ios else {}
    gplay = lookup_google_play(android, runner=gplay_runner) if android else {}
    n = 0
    for app_id, store, store_id in rows:
        meta = itunes.get(str(store_id)) if store == "ios" else gplay.get(str(store_id))
        if not meta:
            continue
        screenshots = getattr(meta, "screenshots", None)
        conn.execute(text(
            "UPDATE public.apps SET name=:n, developer=:d, genre=:g, "
            "release_date=CAST(NULLIF(:r,'') AS date), rating=:ar, rating_count=:rc, "
            "description=COALESCE(:desc, description), "
            "icon_url=COALESCE(:icon, icon_url), "
            "screenshots=COALESCE(CAST(:shots AS jsonb), screenshots), "
            "installs=COALESCE(:installs, installs), "
            "price_label=COALESCE(:price, price_label), "
            "is_game=COALESCE(:is_game, is_game), "
            "updated_at=clock_timestamp() WHERE id=:id"),
            {"n": meta.name or None, "d": meta.seller or None, "g": meta.genre or None,
             "r": meta.release_date or "", "ar": meta.avg_rating, "rc": meta.rating_count,
             "desc": _cap_description(getattr(meta, "description", None)),
             "icon": getattr(meta, "icon_url", None),
             "shots": json.dumps(screenshots) if screenshots else None,
             "installs": getattr(meta, "installs", None),
             "price": getattr(meta, "price_label", None),
             "is_game": getattr(meta, "is_game", None),
             "id": app_id})
        n += 1
    return n
