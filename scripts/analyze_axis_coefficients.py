"""The 6-axis statsmodels Logit, OFF the serving path. Its fitted coefficients are
the GemScore-reweight proposal (interpretable knob: which axes drive the user's
likes). Serving ranking now comes from scripts/score_content_preferences.py.

Run:
    PYTHONPATH=src AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf \\
    .venv/bin/python scripts/analyze_axis_coefficients.py [user_email]
"""
import json
import sys

from sqlalchemy import text

from agf.db.engine import begin, get_engine
from agf.scoring.preference import PreferenceModel

_LABELS = text("SELECT rating, features FROM frontend.app_ratings WHERE user_email=:e")


def _rows(conn, user_email):
    out = []
    for rating, features in conn.execute(_LABELS, {"e": user_email}):
        f = features if isinstance(features, dict) else (
            json.loads(features) if features else {})
        out.append((rating, f))
    return out


def analyze(conn, user_email: str) -> dict:
    model = PreferenceModel.fit(_rows(conn, user_email))
    print(f"[axis] {user_email}: model={model.version} "
          f"coefs={json.dumps(model.coefficients)}", flush=True)
    return {"version": model.version, "coefficients": model.coefficients}


def main() -> int:
    email = sys.argv[1] if len(sys.argv) > 1 else "founder@fielddesk.local"
    with begin(get_engine()) as conn:
        analyze(conn, email)
    return 0


if __name__ == "__main__":
    sys.exit(main())
