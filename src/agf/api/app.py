"""Read-only FastAPI over the product matview (master-plan §6 Plan 7). create_app
takes an Engine so tests inject the migrated test engine; production wires the
AGF_DATABASE_URL engine. Never auto-calls — the only mutation is a human verdict."""
from fastapi import FastAPI
from agf.api.routers import shortlist, gems, review, health


def create_app(engine) -> FastAPI:
    app = FastAPI(title="Field Desk — find idea that already work", version="1.0")
    app.state.engine = engine
    app.include_router(shortlist.router)
    app.include_router(gems.router)
    app.include_router(review.router)
    app.include_router(health.router)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app
