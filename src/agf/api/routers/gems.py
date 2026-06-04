from fastapi import APIRouter, Depends, HTTPException
from agf.api import queries
from agf.api.deps import get_conn
from agf.api.schemas import GemDossier, ScalingPoint, ShortlistRow

router = APIRouter(prefix="/api", tags=["gems"])


@router.get("/gems/{advertiser_id}", response_model=GemDossier)
def get_gem(advertiser_id: int, conn=Depends(get_conn)):
    d = queries.gem_dossier(conn, advertiser_id)
    if d is None:
        raise HTTPException(status_code=404, detail="gem not found. loosen filter.")
    d["gem"] = ShortlistRow(**d["gem"])
    return GemDossier(**d)


@router.get("/gems/{advertiser_id}/scaling", response_model=list[ScalingPoint])
def get_gem_scaling(advertiser_id: int, conn=Depends(get_conn)):
    return [ScalingPoint(**dict(p)) for p in queries.gem_scaling(conn, advertiser_id)]
