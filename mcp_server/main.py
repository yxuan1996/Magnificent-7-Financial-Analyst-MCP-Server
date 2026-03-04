"""
main.py
-------
Entry point for the Magnificent 7 Financial Analyst MCP server.

Startup sequence
~~~~~~~~~~~~~~~~
1. Load settings from .env via config.py
2. Create the FastMCP application
3. Mount JWTAuthMiddleware (Supabase Bearer tokens)
4. Register all tool groups (vector, financial, event, people)
5. Expose a /health endpoint for liveness probes
6. Start the Uvicorn server

Usage
~~~~~
    python main.py

Or via uvicorn directly:
    uvicorn main:app --host 0.0.0.0 --port 8000
"""

import logging
import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from config import settings
from auth import JWTAuthMiddleware
from services.neo4j_service import get_neo4j_service
from services.pinecone_service import get_pinecone_service
from services.auth_service import get_auth_service

from tools.vector_tools import register_vector_tools
from tools.financial_tools import register_financial_tools
from tools.event_tools import register_event_tools
from tools.people_tools import register_people_tools

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
# Lifespan – initialise / teardown shared services
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(_app) -> AsyncIterator[None]:
    logger.info("🚀  Starting %s …", settings.mcp_server_name)

    # Eagerly initialise singletons to surface connection errors at startup.
    get_auth_service()
    logger.info("✅  Supabase auth service ready")

    get_pinecone_service()
    logger.info("✅  Pinecone service ready  (index: %s)", settings.pinecone_index_name)

    get_neo4j_service()
    logger.info("✅  Neo4j service ready  (uri: %s)", settings.neo4j_uri)

    yield

    # Teardown
    svc = get_neo4j_service()
    svc.close()
    logger.info("👋  Neo4j connection closed")


# ---------------------------------------------------------------------------
# FastMCP application
# ---------------------------------------------------------------------------
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
)

# Register all tool groups
register_vector_tools(mcp)
register_financial_tools(mcp)
register_event_tools(mcp)
register_people_tools(mcp)

logger.info(
    "📦  Registered tools: %s",
    [t.name for t in mcp._tools.values()],  # noqa: SLF001
)


# ---------------------------------------------------------------------------
# Health-check endpoint (bypasses JWT middleware)
# ---------------------------------------------------------------------------
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": settings.mcp_server_name})


# ---------------------------------------------------------------------------
# Starlette wrapper with middleware
# ---------------------------------------------------------------------------
# FastMCP exposes a Starlette sub-application; we wrap it with our own
# Starlette app so we can add middleware and extra routes cleanly.

_mcp_app = mcp.get_asgi_app()

app = Starlette(
    lifespan=lifespan,
    routes=[
        Route("/health", health),
        Route("/{path:path}", _mcp_app),  # forward everything else to FastMCP
    ],
)

app.add_middleware(JWTAuthMiddleware)


# ---------------------------------------------------------------------------
# Dev runner
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.mcp_server_host,
        port=settings.mcp_server_port,
        reload=False,
        log_level=settings.mcp_log_level.lower(),
    )