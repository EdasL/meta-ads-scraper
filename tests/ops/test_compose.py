import os
import shutil
import subprocess
from pathlib import Path
import pytest

COMPOSE = Path(__file__).resolve().parents[2] / "ops" / "docker-compose.yml"
# Proxies are in-process (AGF_PROXIES via ProxyPool) — the old `proxy` gateway container was
# removed; `api-py` is the FastAPI fallback alongside the Go `api`.
SERVICES = ("postgres", "minio", "minio-init", "api", "api-py", "worker", "cron")


def test_compose_has_seven_services():
    text = COMPOSE.read_text()
    for svc in SERVICES:
        assert f"  {svc}:" in text, svc
    assert "  proxy:" not in text          # no proxy gateway container — proxies are in-process


def test_compose_creds_are_env_only():
    text = COMPOSE.read_text()
    assert "${POSTGRES_PASSWORD}" in text and "${MINIO_ROOT_PASSWORD}" in text
    assert "minioadmin" not in text          # no weak hardcoded default creds
    assert "depends_on" in text and "pg_data" in text and "minio_data" in text


def test_compose_validates():
    if not shutil.which("docker"):
        pytest.skip("docker not available — static checks only")
    env = {**os.environ, "POSTGRES_PASSWORD": "x", "MINIO_ROOT_PASSWORD": "y",
           "POSTGRES_USER": "agf", "POSTGRES_DB": "agf", "MINIO_ROOT_USER": "agf"}
    r = subprocess.run(["docker", "compose", "-f", str(COMPOSE), "config", "-q"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
