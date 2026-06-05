"""GemScore priors calibration report — synthetic-trajectory backtest.

Usage:
    export AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf
    export PYTHONPATH=src
    .venv/bin/python scripts/backtest.py

What it does:
1. Seeds the labeled synthetic corpus into Postgres.
2. Runs the REAL score_all (pctile within cohort → gem_score → vetoes).
3. Prints:
   - Per-label gem_score distribution (min / median / max) and veto breakdown.
   - Precision/recall sweep over ALERT_THRESHOLD in [50, 60, 70, 80, 90].
   - Veto-confusion table (rows=label, cols=veto_reason).
   - Sensitivity check: alternate SV/UR/FI exponent sets and breakout vs
     incumbent ordering.
4. Cleans up all BT* sentinels before exiting.

Notes:
- Does NOT mutate any shipping constant on disk.
- Sensitivity sweeps recompute locally; they do not re-run score_all.
"""
from __future__ import annotations

import os
import sys
import statistics
from collections import defaultdict
from datetime import date, datetime, timezone, timedelta

# ---------------------------------------------------------------------------
# Guard: require AGF_TEST_DB_URL
# ---------------------------------------------------------------------------
_DB_URL = os.environ.get("AGF_TEST_DB_URL")
if not _DB_URL:
    print(
        "ERROR: AGF_TEST_DB_URL is not set.\n"
        "  export AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf"
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# Imports (after env check so missing modules don't shadow the clear error)
# ---------------------------------------------------------------------------
from sqlalchemy import text

from agf.db import repo
from agf.db.engine import get_engine, begin
from agf.parse import AdRecord
from agf.scoring.run_scoring import score_all, refresh_emerging_matview
from agf.scoring.gemscore import gem_score as _gem_score_fn, clamp01

# ---------------------------------------------------------------------------
# Corpus definition (mirrors tests/scoring/test_backtest_synthetic.py)
# ---------------------------------------------------------------------------
AS_OF = date(2026, 6, 5)
NOW = int(datetime(2026, 6, 5, tzinfo=timezone.utc).timestamp())
DAY = 86_400
WEEK0 = date(2026, 2, 2)

SENTINELS = (
    "BT01", "BT02", "BT03", "BT04", "BT05", "BT06", "BT07", "BT08",
    "BT11", "BT12", "BT13", "BT14", "BT15", "BT16", "BT17", "BT18",
)

# label assigned to each sentinel
LABELS: dict[str, str] = {
    "BT01": "true_breakout",
    "BT02": "true_breakout",
    "BT03": "incumbent",
    "BT04": "flat_advertiser",
    "BT05": "disguised_incumbent",
    "BT06": "relaunch",
    "BT07": "seasonal_only",
    "BT08": "seasonal_with_trend",
    "BT11": "true_breakout",
    "BT12": "true_breakout",
    "BT13": "incumbent",
    "BT14": "flat_advertiser",
    "BT15": "one_week_blip",
    "BT16": "short_longevity",
    "BT17": "disguised_incumbent",
    "BT18": "relaunch",
}


def _convex_rising(n=10, start=3, factor=1.6):
    s, v = [], float(start)
    for _ in range(n):
        s.append(max(1, int(v)))
        v *= factor
    return s


def _flat(n=10, level=80):
    return [level] * n


def _seasonal_no_trend(n=12, period=4):
    base = [3, 1, 2, 6]
    return (base * (n // period + 1))[:n]


def _seasonal_with_trend(n=12, period=4):
    base = [3, 1, 2, 6]
    raw = (base * (n // period + 1))[:n]
    return [v + i * 2 for i, v in enumerate(raw)]


HF_SPECS = {
    "BT01": ("Health & Fitness", 50,        "2026-05-01", True,  35,
             _convex_rising(10), 100, True, "INSTALL_MOBILE_APP", None),
    "BT02": ("Health & Fitness", 70,        "2026-04-15", True,  51,
             _convex_rising(10, start=2, factor=1.8), 70, True, "INSTALL_MOBILE_APP", None),
    "BT03": ("Health & Fitness", 9_000_000, "2016-01-01", False, 500,
             _flat(10, 90), 12_000_000, False, "LEARN_MORE", None),
    "BT04": ("Health & Fitness", 5_000,     "2025-01-01", True,  150,
             _flat(10, 40), 20_000, False, "LEARN_MORE", None),
    "BT05": ("Health & Fitness", 300,       "2026-03-01", True,  96,
             _convex_rising(10), 200, True, "INSTALL_MOBILE_APP", "BT03"),
    "BT06": ("Health & Fitness", 150_000,   "2026-02-01", True,  126,
             _convex_rising(10), 300, True, "INSTALL_MOBILE_APP", None),
    "BT07": ("Health & Fitness", 400,       "2025-10-01", True,  250,
             _seasonal_no_trend(12), 500, True, "INSTALL_MOBILE_APP", None),
    "BT08": ("Health & Fitness", 350,       "2025-10-01", True,  250,
             _seasonal_with_trend(12), 400, True, "INSTALL_MOBILE_APP", None),
}

ED_SPECS = {
    "BT11": ("Education", 60,        "2026-05-01", True,  35,
             _convex_rising(10), 120, True, "INSTALL_MOBILE_APP", None),
    "BT12": ("Education", 65,        "2026-04-20", True,  45,
             _convex_rising(10, start=2, factor=1.8), 75, True, "INSTALL_MOBILE_APP", None),
    "BT13": ("Education", 7_000_000, "2015-06-01", False, 600,
             _flat(10, 85), 10_000_000, False, "LEARN_MORE", None),
    "BT14": ("Education", 4_000,     "2025-02-01", True,  145,
             _flat(10, 35), 15_000, False, "LEARN_MORE", None),
    "BT15": ("Education", 200,       "2026-05-10", True,  20,
             [5], 200, True, "INSTALL_MOBILE_APP", None),
    "BT16": ("Education", 100,       "2026-05-25", True,  10,
             _convex_rising(8), 150, True, "INSTALL_MOBILE_APP", None),
    "BT17": ("Education", 250,       "2026-03-10", True,  87,
             _convex_rising(10), 180, True, "INSTALL_MOBILE_APP", "BT13"),
    "BT18": ("Education", 120_000,   "2026-01-15", True,  141,
             _convex_rising(10), 220, True, "INSTALL_MOBILE_APP", None),
}

ALL_SPECS = {**HF_SPECS, **ED_SPECS}


# ---------------------------------------------------------------------------
# Seed / cleanup
# ---------------------------------------------------------------------------

def _seed(conn):
    ids = {}
    for pid, spec in ALL_SPECS.items():
        genre, ratings, rel, emerging, longd, series, reach, funnel, cta, _parent = spec
        ar = AdRecord(
            f"{pid}-AD", pid, pid, True,
            NOW - longd * DAY, None, 5, cta, "Install",
            f"https://apps.apple.com/us/app/x/id{pid}", "body",
        )
        adv = repo.upsert_advertiser(conn, ar)
        app = repo.upsert_app(conn, {
            "store": "ios", "store_id": pid, "name": pid,
            "genre": genre, "rating_count": ratings,
            "release_date": rel, "is_emerging": emerging,
        })
        ad_id = repo.upsert_ad(conn, ar, adv, app)
        if funnel:
            repo.upsert_funnel(conn, ad_id,
                               f"https://{pid}.example",
                               f"https://{pid}.example/checkout",
                               "shot.png")
        for i, n in enumerate(series):
            repo.upsert_app_advertiser_metric(
                conn, adv, WEEK0 + timedelta(weeks=i), n, n, reach
            )
        ids[pid] = adv
    return ids


def _apply_parent_links(conn, ids):
    for pid, spec in ALL_SPECS.items():
        _parent = spec[9]
        if _parent is not None:
            repo.upsert_advertiser_enrichment(
                conn, ids[pid], parent_company_id=ids[_parent]
            )


def _cleanup(engine):
    with begin(engine) as conn:
        adv_ids = conn.execute(
            text("SELECT id FROM public.advertisers WHERE page_id = ANY(:p)"),
            {"p": list(SENTINELS)},
        ).scalars().all()
        if adv_ids:
            conn.execute(text(
                "DELETE FROM adtech.advertiser_scores WHERE advertiser_id = ANY(:i)"
            ), {"i": adv_ids})
            conn.execute(text(
                "DELETE FROM adtech.app_advertiser_metrics WHERE advertiser_id = ANY(:i)"
            ), {"i": adv_ids})
            conn.execute(text(
                "DELETE FROM adtech.ad_metrics m USING public.ads a "
                "WHERE m.ad_id=a.id AND a.advertiser_id = ANY(:i)"
            ), {"i": adv_ids})
            conn.execute(text(
                "DELETE FROM public.funnels f USING public.ads a "
                "WHERE f.ad_id=a.id AND a.advertiser_id = ANY(:i)"
            ), {"i": adv_ids})
            conn.execute(text(
                "DELETE FROM public.creatives c USING public.ads a "
                "WHERE c.ad_id=a.id AND a.advertiser_id = ANY(:i)"
            ), {"i": adv_ids})
            conn.execute(text(
                "DELETE FROM public.ads WHERE advertiser_id = ANY(:i)"
            ), {"i": adv_ids})
            conn.execute(text(
                "DELETE FROM public.advertisers WHERE id = ANY(:i)"
            ), {"i": adv_ids})
        conn.execute(
            text("DELETE FROM public.apps WHERE store_id = ANY(:p)"),
            {"p": list(SENTINELS)},
        )
    refresh_emerging_matview(engine)


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------

def _pct_sweep(results: list[dict], thresholds=(50, 60, 70, 80, 90)):
    """
    Precision/recall sweep treating label=='true_breakout' as positive class.
    results: list of {pid, label, gem_score, vetoed, alert}
    Positive = label is true_breakout AND NOT vetoed (vetoed ones can't alert).
    """
    positives = {r["pid"] for r in results if r["label"] == "true_breakout" and not r["vetoed"]}
    rows = []
    for t in thresholds:
        alerted = {r["pid"] for r in results if r["gem_score"] >= t and not r["vetoed"]}
        tp = len(alerted & positives)
        fp = len(alerted - positives)
        fn = len(positives - alerted)
        precision = tp / (tp + fp) if (tp + fp) > 0 else float("nan")
        recall = tp / (tp + fn) if (tp + fn) > 0 else float("nan")
        rows.append((t, tp, fp, fn, precision, recall))
    return rows


def _sensitivity(sv_exp, ur_exp, fi_exp, breakout_gs, incumbent_gs):
    """Recompute gem_score under alternate exponents (in-memory only)."""
    def gs(sv, ur, fi):
        a = 0.05 if sv is None else clamp01(sv)
        b = 0.05 if ur is None else clamp01(ur)
        c = 0.05 if fi is None else clamp01(fi)
        return 100.0 * (a ** sv_exp) * (b ** ur_exp) * (c ** fi_exp)

    return [(lbl, gs(sv, ur, fi)) for lbl, sv, ur, fi in breakout_gs + incumbent_gs]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    engine = get_engine(_DB_URL)
    _cleanup(engine)   # pre-clean in case of a prior crash

    print("=" * 70)
    print("GemScore priors backtest — synthetic corpus")
    print(f"AS_OF: {AS_OF}   Corpus: {len(ALL_SPECS)} advertisers in 2 cohorts")
    print("=" * 70)

    try:
        # ---- Seed & score ------------------------------------------------
        with begin(engine) as conn:
            ids = _seed(conn)
            _apply_parent_links(conn, ids)
            # only_ids isolates scoring + cohort normalization to THIS corpus, so the
            # report is reproducible and never overwrites other advertisers' scores
            # (score_all is global by default — it would otherwise mix in any rows
            # sharing the Health & Fitness / Education cohorts).
            n_scored = score_all(conn, AS_OF, only_ids=list(ids.values()))
        refresh_emerging_matview(engine)
        print(f"\nScored {n_scored} advertisers (isolated to the synthetic corpus).\n")

        # ---- Fetch results -----------------------------------------------
        pid_to_id = ids   # {page_id: advertiser_id}
        id_to_pid = {v: k for k, v in pid_to_id.items()}

        with engine.connect() as conn:
            raw = conn.execute(
                text(
                    "SELECT advertiser_id, gem_score, vetoed, veto_reason, alert, "
                    "scaling_velocity, under_radar, funnel_intent "
                    "FROM adtech.advertiser_scores WHERE advertiser_id = ANY(:i)"
                ),
                {"i": list(ids.values())},
            ).all()

        results = []
        for row in raw:
            pid = id_to_pid.get(row.advertiser_id, "?")
            results.append({
                "pid": pid,
                "adv_id": row.advertiser_id,
                "label": LABELS.get(pid, "unknown"),
                "gem_score": float(row.gem_score),
                "vetoed": bool(row.vetoed),
                "veto_reason": row.veto_reason,
                "alert": bool(row.alert),
                "sv": row.scaling_velocity,
                "ur": row.under_radar,
                "fi": row.funnel_intent,
            })
        results.sort(key=lambda r: r["gem_score"], reverse=True)

        # ---- Per-label distribution table --------------------------------
        print("─" * 70)
        print("PER-LABEL GemScore DISTRIBUTION")
        print("─" * 70)
        by_label: dict[str, list] = defaultdict(list)
        for r in results:
            by_label[r["label"]].append(r)

        label_order = [
            "true_breakout", "incumbent", "flat_advertiser",
            "seasonal_only", "seasonal_with_trend",
            "disguised_incumbent", "relaunch",
            "one_week_blip", "short_longevity",
        ]
        hdr = f"{'Label':<25} {'N':>2}  {'Min':>6}  {'Median':>6}  {'Max':>6}  {'Veto reason(s)'}"
        print(hdr)
        print("-" * 70)
        for lbl in label_order:
            members = by_label.get(lbl, [])
            if not members:
                continue
            scores = [m["gem_score"] for m in members]
            reasons = sorted({m["veto_reason"] or "none" for m in members})
            med = statistics.median(scores)
            print(
                f"{lbl:<25} {len(members):>2}  {min(scores):>6.1f}  {med:>6.1f}  "
                f"{max(scores):>6.1f}  {', '.join(reasons)}"
            )

        # ---- Full per-advertiser table -----------------------------------
        print()
        print("─" * 70)
        print("PER-ADVERTISER SCORES")
        print("─" * 70)
        print(f"{'PID':<6}  {'Label':<22} {'GemScore':>8}  {'SV':>5}  {'UR':>5}  "
              f"{'FI':>5}  {'Alert':>5}  {'VetoReason'}")
        print("-" * 80)
        for r in results:
            sv_s = f"{r['sv']:.2f}" if r['sv'] is not None else "  N/A"
            ur_s = f"{r['ur']:.2f}" if r['ur'] is not None else "  N/A"
            fi_s = f"{r['fi']:.2f}" if r['fi'] is not None else "  N/A"
            print(
                f"{r['pid']:<6}  {r['label']:<22} {r['gem_score']:>8.1f}  "
                f"{sv_s:>5}  {ur_s:>5}  {fi_s:>5}  "
                f"{'YES' if r['alert'] else 'no':>5}  {r['veto_reason'] or '-'}"
            )

        # ---- Precision / recall sweep ------------------------------------
        print()
        print("─" * 70)
        print("PRECISION / RECALL SWEEP  (positive class = true_breakout, unvetoed)")
        print("─" * 70)
        sweep = _pct_sweep(results)
        print(f"{'Threshold':>9}  {'TP':>4}  {'FP':>4}  {'FN':>4}  "
              f"{'Precision':>9}  {'Recall':>9}")
        print("-" * 55)
        for t, tp, fp, fn, prec, rec in sweep:
            prec_s = f"{prec:.3f}" if prec == prec else "   N/A"
            rec_s = f"{rec:.3f}" if rec == rec else "   N/A"
            marker = " <-- shipping" if t == 70 else ""
            print(f"{t:>9}  {tp:>4}  {fp:>4}  {fn:>4}  {prec_s:>9}  {rec_s:>9}{marker}")

        # ---- Veto-confusion table ----------------------------------------
        print()
        print("─" * 70)
        print("VETO-CONFUSION TABLE  (rows=label, cols=veto_reason)")
        print("─" * 70)
        all_reasons = sorted({r["veto_reason"] or "none" for r in results})
        col_w = max(len(c) for c in all_reasons + ["veto_reason"])
        header_parts = [f"{'Label':<25}"] + [f"{c:>{col_w}}" for c in all_reasons]
        print("  ".join(header_parts))
        print("-" * 70)
        for lbl in label_order:
            members = by_label.get(lbl, [])
            if not members:
                continue
            counts: dict[str, int] = defaultdict(int)
            for m in members:
                counts[m["veto_reason"] or "none"] += 1
            row_parts = [f"{lbl:<25}"] + [
                f"{counts.get(c, 0):>{col_w}}" for c in all_reasons
            ]
            print("  ".join(row_parts))

        # ---- Sensitivity sweep -------------------------------------------
        print()
        print("─" * 70)
        print("SENSITIVITY: breakout vs incumbent under alternate exponents")
        print("  (in-memory recomputation; does not re-run score_all)")
        print("─" * 70)

        # Collect SV/UR/FI from the DB results for a breakout and incumbent pair
        def _axis(pid, ax):
            for r in results:
                if r["pid"] == pid:
                    return r[ax]
            return None

        # Representative pair from Health & Fitness
        pairs = [("BT01", "BT03"), ("BT11", "BT13")]
        alt_exps = [
            ("shipping (SV=.50 UR=.35 FI=.15)", 0.50, 0.35, 0.15),
            ("SV-heavy  (SV=.60 UR=.25 FI=.15)", 0.60, 0.25, 0.15),
            ("UR-heavy  (SV=.40 UR=.45 FI=.15)", 0.40, 0.45, 0.15),
            ("balanced  (SV=.40 UR=.40 FI=.20)", 0.40, 0.40, 0.20),
        ]
        print(f"{'Exponent set':<38}  {'Pair':<12}  {'Breakout':>8}  {'Incumbent':>9}  {'B>I?':>5}")
        print("-" * 80)
        # mirror gem_score's imputation EXACTLY: a MISSING axis (None) → floor 0.05;
        # a genuinely-zero axis stays 0.0 (it must gate the product to 0). `or 0.05`
        # wrongly lifted a real 0.0 to the floor.
        def _imp(pid, ax):
            v = _axis(pid, ax)
            return 0.05 if v is None else v

        for label, sv_e, ur_e, fi_e in alt_exps:
            for bp, ip in pairs:
                sv_b, ur_b, fi_b = _imp(bp, "sv"), _imp(bp, "ur"), _imp(bp, "fi")
                sv_i, ur_i, fi_i = _imp(ip, "sv"), _imp(ip, "ur"), _imp(ip, "fi")

                def _gs(sv, ur, fi):
                    return 100.0 * (clamp01(sv) ** sv_e) * (clamp01(ur) ** ur_e) * (clamp01(fi) ** fi_e)

                gs_b = _gs(sv_b, ur_b, fi_b)
                gs_i = _gs(sv_i, ur_i, fi_i)
                ok = "YES" if gs_b > gs_i else "NO "
                print(f"{label:<38}  {bp} vs {ip}  {gs_b:>8.1f}  {gs_i:>9.1f}  {ok:>5}")

        print()
        print("=" * 70)
        print("Done. Cleaning up sentinels…")

    finally:
        _cleanup(engine)
        print("Sentinels removed. DB restored to pre-backtest state.")
        print("=" * 70)


if __name__ == "__main__":
    main()
