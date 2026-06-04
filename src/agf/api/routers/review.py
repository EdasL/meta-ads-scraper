from fastapi import APIRouter, Depends
from agf.api import queries
from agf.api.deps import get_conn, get_write_conn
from agf.api.schemas import ReviewQueueRow, ReviewDecisionIn

router = APIRouter(prefix="/api", tags=["review"])


@router.get("/review/queue", response_model=list[ReviewQueueRow])
def get_queue(min_score: float = 70.0, limit: int = 50, offset: int = 0,
              conn=Depends(get_conn)):
    return [ReviewQueueRow(**dict(r)) for r in
            queries.review_queue(conn, min_score=min_score, limit=limit, offset=offset)]


@router.post("/review/{advertiser_id}/decision")
def post_decision(advertiser_id: int, body: ReviewDecisionIn, conn=Depends(get_write_conn)):
    queries.record_decision(conn, advertiser_id, body.verdict, body.reviewer, body.note)
    return {"status": "ok", "advertiser_id": advertiser_id, "verdict": body.verdict}
