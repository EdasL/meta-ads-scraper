"""Read-only FastAPI over the product matview (master-plan §6 Plan 7). create_app
takes an Engine so tests inject the migrated test engine; production wires the
AGF_DATABASE_URL engine. Never auto-calls — the only mutation is a human verdict."""
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from agf.api.routers import shortlist, gems, review, health, media

# The Next.js dossier UI calls this API from the browser, so cross-origin must be
# allowed. AGF_CORS_ORIGINS is a comma-separated allowlist; defaults to the local
# `next dev` origin so the founder's web app works out of the box.
_DEFAULT_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"


def _cors_origins() -> list[str]:
    raw = os.environ.get("AGF_CORS_ORIGINS", _DEFAULT_ORIGINS)
    return [o.strip() for o in raw.split(",") if o.strip()]


def create_app(engine) -> FastAPI:
    app = FastAPI(title="Field Desk — find idea that already work", version="1.0")
    app.state.engine = engine
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )
    app.include_router(shortlist.router)
    app.include_router(gems.router)
    app.include_router(review.router)
    app.include_router(health.router)
    app.include_router(media.router)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app
