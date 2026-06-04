from dataclasses import asdict
from fastapi import APIRouter, Depends
from agf.api.deps import get_conn
from agf.monitoring.metrics import compute_metrics, rotation_alerts

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health/metrics")
def health_metrics(conn=Depends(get_conn)):
    m = compute_metrics(conn)
    m["alerts"] = [asdict(a) for a in rotation_alerts(conn)]
    m["degraded"] = bool(m["alerts"])
    return m
