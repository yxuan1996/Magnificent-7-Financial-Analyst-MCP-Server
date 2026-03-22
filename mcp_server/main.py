"""
main.py
-------
Entry point for the Magnificent 7 Financial Analyst MCP server.

Startup sequence
~~~~~~~~~~~~~~~~
1. Load settings from .env via config.py
2. Create the FastMCP application
3. Register lifespan hooks for service initialisation / teardown
4. Register all tool groups (vector, financial, event, people)
5. Add CORS middleware (allow all origins)
6. Start via mcp.run(transport="http")


Running the server
~~~~~~~~~~~~~~~~~~
    python main.py

Health check
~~~~~~~~~~~~
    GET /health  — no authentication required
    Returns JSON with server name, status, uptime, and per-service readiness.
    Suitable for use as a liveness or readiness probe (HTTP 200 = healthy).
"""

import logging
import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator

import time
from fastmcp import FastMCP
from fastmcp.server.middleware.logging import StructuredLoggingMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from config import settings
from services.neo4j_service import get_neo4j_service
from services.pinecone_service import get_pinecone_service

from tools.vector_tools import register_vector_tools
from tools.financial_tools import register_financial_tools
from tools.event_tools import register_event_tools
from tools.people_tools import register_people_tools
from tools.graph_tools import register_graph_tools, TOOL_INSPECT_GRAPH, TOOL_RUN_CYPHER

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    stream=sys.stdout,
    level=getattr(logging, settings.mcp_log_level.upper(), logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan — service initialisation and graceful teardown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(_app) -> AsyncIterator[None]:
    logger.info("🚀  Starting %s …", settings.mcp_server_name)

    get_pinecone_service()
    logger.info("✅  Pinecone service ready  (index: %s)", settings.pinecone_index_name)

    get_neo4j_service()
    logger.info("✅  Neo4j service ready  (uri: %s)", settings.neo4j_uri)

    yield

    get_neo4j_service().close()
    logger.info("👋  Neo4j connection closed")


# ---------------------------------------------------------------------------
# FastMCP application
# ---------------------------------------------------------------------------

# Record the time the server process started so /health can report uptime.
_SERVER_START = time.time()

mcp = FastMCP(
    name=settings.mcp_server_name,
    instructions=(
        "You are a financial analysis assistant for the Magnificent 7 tech "
        "companies (AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA). "
        "You have access to their annual report text, financial tables, "
        "structured financial facts, key corporate developments, and "
        "key leadership information extracted from Form 10-K filings. "
        "Always cite the ticker and fiscal year when presenting financial data."
    ),
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Middleware  (MCP-level)
# ---------------------------------------------------------------------------
mcp.add_middleware(StructuredLoggingMiddleware())

# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------
register_vector_tools(mcp)
register_financial_tools(mcp)
register_event_tools(mcp)
register_people_tools(mcp)
register_graph_tools(mcp)

# Log registered tool names using the public constants from each tool module
# rather than private FastMCP internals, which vary between versions.
from tools.vector_tools import TOOL_SEARCH_TEXT, TOOL_SEARCH_TABLES
from tools.financial_tools import TOOL_GET_FINANCIAL_METRIC, TOOL_COMPARE_YEARS, TOOL_COMPARE_COMPANIES
from tools.event_tools import TOOL_GET_KEY_DEVELOPMENTS
from tools.people_tools import TOOL_GET_KEY_PERSONS
from tools.graph_tools import TOOL_INSPECT_GRAPH, TOOL_RUN_CYPHER

_REGISTERED_TOOLS = [
    TOOL_SEARCH_TEXT,
    TOOL_SEARCH_TABLES,
    TOOL_GET_FINANCIAL_METRIC,
    TOOL_COMPARE_YEARS,
    TOOL_COMPARE_COMPANIES,
    TOOL_GET_KEY_DEVELOPMENTS,
    TOOL_GET_KEY_PERSONS,
    TOOL_INSPECT_GRAPH,
    TOOL_RUN_CYPHER,
]
logger.info("📦  Registered tools: %s", _REGISTERED_TOOLS)

# ---------------------------------------------------------------------------
# Health check endpoint
# ---------------------------------------------------------------------------
# Registered as a plain HTTP route on the FastMCP ASGI app.
# Authentication is NOT required — this endpoint is intended for:
#   • Deployment health / liveness probes (Kubernetes, Render, Railway …)
#   • Smoke-testing that the server started correctly after a deploy
#   • Verifying downstream service connectivity before sending real queries
#
# Example:
#   curl https://your-mcp-server.example.com/health

@mcp.custom_route("/health", methods=["GET"])
async def health_check(_: Request) -> JSONResponse:
    """
    Returns a JSON object describing the server's current health.

    HTTP 200  — server is up; individual services may still be degraded.
    HTTP 503  — one or more critical services are unreachable.

    Response fields:
        status      "ok" | "degraded"
        server      server name from settings
        uptime_s    seconds since the process started
        services    dict mapping service name → "ok" | "error: <message>"
    """
    services: dict = {}

    # ── Pinecone ────────────────────────────────────────────────────────
    try:
        from services.pinecone_service import get_pinecone_service
        pc = get_pinecone_service()
        # describe_index_stats() is the cheapest Pinecone read operation.
        pc._index.describe_index_stats()
        services["pinecone"] = "ok"
    except Exception as exc:
        services["pinecone"] = f"error: {exc}"

    # ── Neo4j ───────────────────────────────────────────────────────────
    try:
        neo = get_neo4j_service()
        neo._run("RETURN 1 AS ok")
        services["neo4j"] = "ok"
    except Exception as exc:
        services["neo4j"] = f"error: {exc}"

    all_ok = all(v == "ok" for v in services.values())
    status_code = 200 if all_ok else 503

    return JSONResponse(
        status_code=status_code,
        content={
            "status":   "ok" if all_ok else "degraded",
            "server":   settings.mcp_server_name,
            "uptime_s": round(time.time() - _SERVER_START, 1),
            "services": services,
        },
    )


# ---------------------------------------------------------------------------
# HTTP-level middleware (Starlette layer)
# ---------------------------------------------------------------------------
# Applied to the underlying Starlette ASGI app so that every HTTP response
# — including /health and all MCP endpoints — carries CORS headers.

_http_app = mcp.http_app()
_http_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],    # tighten to specific origins in production
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,  # must be False when allow_origins=["*"]
)
logger.info("🌐  CORSMiddleware registered (allow_origins=['*'])")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    mcp.run(
        transport="http",
        host=settings.mcp_server_host,
        port=settings.mcp_server_port,
        log_level=settings.mcp_log_level.lower(),
    )