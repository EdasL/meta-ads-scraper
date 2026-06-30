"""MCP server exposing read-only scraping-health metrics.

Run standalone:  PYTHONPATH=src python -m agf_mcp.server
Registered for Claude Code via the project .mcp.json.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import metrics
from .db import DBError

mcp = FastMCP("agf-scraping")


def _safe(fn, **kwargs):
    try:
        return fn(**kwargs)
    except DBError as exc:
        return {"error": str(exc)}


@mcp.tool()
def engine_pulse() -> dict:
    """Is the scraping engine alive or wedged? Newest candidate timestamp,
    triage backlog, and recent sweep/flywheel/funnel run activity."""
    return _safe(metrics.engine_pulse)


@mcp.tool()
def discovery_rate(days: int = 14) -> dict:
    """New candidates discovered per day over `days`, status mix + kept-rate,
    and distinct source-keyword breadth."""
    return _safe(metrics.discovery_rate, days=days)


@mcp.tool()
def idea_diversity() -> dict:
    """Semantic diversity of discovered app ideas (from store-listing
    embeddings): effective number of concepts, cluster concentration for the
    corpus and for gems, human-readable concept groups, embedding coverage, and
    whether the newest finds are broadening into new ideas or rehashing existing
    ones."""
    return _safe(metrics.idea_diversity)


@mcp.tool()
def scraping_overview() -> dict:
    """One-call dashboard: engine pulse + discovery rate + idea diversity."""
    return _safe(metrics.scraping_overview)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
