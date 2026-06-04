from fastapi import APIRouter, Depends
from agf.api import queries
from agf.api.deps import get_conn
from agf.api.schemas import ShortlistRow

router = APIRouter(prefix="/api", tags=["shortlist"])


@router.get("/shortlist", response_model=list[ShortlistRow])
def get_shortlist(cohort: str | None = None, min_score: float = 0.0,
                  limit: int = 50, offset: int = 0, conn=Depends(get_conn)):
    return [ShortlistRow(**dict(r)) for r in
            queries.shortlist(conn, cohort=cohort, min_score=min_score,
                              limit=limit, offset=offset)]
