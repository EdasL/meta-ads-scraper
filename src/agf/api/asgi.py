"""Production ASGI entrypoint: `uvicorn agf.api.asgi:app`. Builds the read-only app
over the engine from AGF_DATABASE_URL (create_engine is lazy — no connection at import)."""
from agf.db.engine import get_engine
from agf.api.app import create_app

app = create_app(get_engine())
