"""Read-only S3 media proxy. The web dossier renders creative images + funnel
screenshots, but the MinIO/S3 endpoint is internal to the compose network and
unreachable from the browser, so the API streams the bytes. Only the two known
object prefixes are served — a path-traversal / arbitrary-key-read guard.

The web client requests `${API_BASE}/media/<s3_key>` (see web/src/lib/adapters.ts
`mediaUrl`); this serves exactly those keys.

NOTE (provenance): added 2026-06-05 by the web/loop agent to unblock the live
image feature while the backend lane was idle; built to the spec recorded in the
project memory `web-live-wiring`. Backend owner: please review/adopt. `get_s3()`
re-validates the bucket per call (fine for dev; cache the client if it gets hot).
"""
import re
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from botocore.exceptions import ClientError
from agf.storage.s3 import get_s3, _BUCKET

router = APIRouter(prefix="/api", tags=["media"])

# Anchored: must start with a known prefix, no leading slash, no "..". This is the
# whole authorization model — without it any caller could read arbitrary objects.
_ALLOWED_KEY = re.compile(r"^(creatives|funnels)/[A-Za-z0-9][A-Za-z0-9._/-]*$")


@router.get("/media/{key:path}")
def get_media(key: str):
    if ".." in key or not _ALLOWED_KEY.match(key):
        raise HTTPException(status_code=403, detail="nope. not that.")
    try:
        obj = get_s3().get_object(Bucket=_BUCKET(), Key=key)
    except ClientError:
        raise HTTPException(status_code=404, detail="grug not find that.")
    ctype = obj.get("ContentType") or "application/octet-stream"
    return StreamingResponse(obj["Body"].iter_chunks(), media_type=ctype)
