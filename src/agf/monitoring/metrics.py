"""Ops metrics + rotation alerts over logging.crawl_log (master-plan §6 P7-T9).
doc_id rotation and rate-limit are the two maintenance-treadmill signals (§2)."""
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from sqlalchemy import text


@dataclass(frozen=True)
class Alert:
    kind: str          # 'doc_id_rotation' | 'rate_limit' | 'stale_snapshot'
    message: str
    severity: str = "warning"


def _since(window):
    return datetime.now(timezone.utc) - window


def compute_metrics(conn, *, window=timedelta(hours=24)) -> dict:
    since = _since(window)
    rows = conn.execute(text(
        "SELECT crawl_result, count(*) FROM logging.crawl_log "
        "WHERE crawled_at >= :s GROUP BY crawl_result"), {"s": since}).all()
    total = sum(c for _, c in rows)
    errors = sum(c for r, c in rows if r and r.lower().startswith("error"))
    return {"window_hours": window.total_seconds() / 3600, "total": total,
            "errors": errors, "ok": total - errors}


def _count(conn, since, needles):
    rows = conn.execute(text(
        "SELECT crawl_result FROM logging.crawl_log WHERE crawled_at >= :s "
        "AND crawl_result IS NOT NULL"), {"s": since}).scalars().all()
    return sum(1 for r in rows if any(n in r.lower() for n in needles))


def rotation_alerts(conn, *, window=timedelta(hours=1), doc_id_threshold=5,
                    rate_limit_threshold=10) -> list:
    since = _since(window)
    alerts = []
    docid = _count(conn, since, ("doc_id", "http 400", "400 ", "1357004", "1357001"))
    if docid >= doc_id_threshold:
        alerts.append(Alert("doc_id_rotation",
                            f"{docid} doc_id/400 failures in {window} — re-heal token/doc_id",
                            "critical"))
    rl = _count(conn, since, ("429", "rate_limit", "1675004"))
    if rl >= rate_limit_threshold:
        alerts.append(Alert("rate_limit",
                            f"{rl} rate-limit hits in {window} — back off / rotate proxies"))
    return alerts
