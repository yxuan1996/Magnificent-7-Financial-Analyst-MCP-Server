"""
main.py
-------
Entry point for the Magnificent 7 Financial Analyst MCP server.
 
Startup sequence
~~~~~~~~~~~~~~~~
1. Load settings from .env via config.py
2. Create the FastMCP application
3. Attach middleware via mcp.add_middleware()
4. Register lifespan hooks for service initialisation / teardown
5. Register all tool groups (vector, financial, event, people)
6. Expose ``app`` (standard ASGI callable) for uvicorn
 
Auth flow per tool call
~~~~~~~~~~~~~~~~~~~~~~~~
    Client sends Bearer JWT
        → AuthMiddleware.on_call_tool
            → Supabase: verify JWT signature + expiry
            → Supabase RBAC: check role_permissions for tool access
            → Supabase RBAC: derive allowed tickers from role names
            → UserContext stored in ContextVar
        → Tool handler reads UserContext via get_current_user()
 
Running the server
~~~~~~~~~~~~~~~~~~
    # Directly (development)
    python main.py
 
    # Via uvicorn CLI (production / behind a reverse proxy)
    uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
 
    Note: use --workers 1. FastMCP maintains in-process state (ContextVar,
    singleton service objects) that is not safe to share across OS processes.
    For horizontal scaling, run multiple single-worker instances behind a
    load balancer instead.
"""

import logging
import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator

import uvicorn
from starlette.middleware.cors import CORSMiddleware
from fastmcp import FastMCP
from fastmcp.server.middleware.logging import StructuredLoggingMiddleware

from config import settings
from auth import AuthMiddleware
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
# Lifespan — service initialisation and graceful teardown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(_app) -> AsyncIterator[None]:
    logger.info("🚀  Starting %s …", settings.mcp_server_name)

    get_auth_service()
    logger.info("✅  Supabase auth + RBAC service ready")

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
# Middleware  (order matters — applied outermost-first)
# ---------------------------------------------------------------------------
# 1. StructuredLoggingMiddleware — logs every tool call with timing
# 2. AuthMiddleware              — Supabase JWT + RBAC policy enforcement

mcp.add_middleware(StructuredLoggingMiddleware())
mcp.add_middleware(AuthMiddleware())

logger.info("🔒  AuthMiddleware registered (Supabase JWT + RBAC)")

# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------
register_vector_tools(mcp)
register_financial_tools(mcp)
register_event_tools(mcp)
register_people_tools(mcp)

# Log registered tool names using the public constants from each tool module
# rather than private FastMCP internals, which vary between versions.
from tools.vector_tools import TOOL_SEARCH_TEXT, TOOL_SEARCH_TABLES
from tools.financial_tools import TOOL_GET_FINANCIAL_METRIC, TOOL_COMPARE_YEARS, TOOL_COMPARE_COMPANIES
from tools.event_tools import TOOL_GET_KEY_DEVELOPMENTS
from tools.people_tools import TOOL_GET_KEY_PERSONS

_REGISTERED_TOOLS = [
    TOOL_SEARCH_TEXT,
    TOOL_SEARCH_TABLES,
    TOOL_GET_FINANCIAL_METRIC,
    TOOL_COMPARE_YEARS,
    TOOL_COMPARE_COMPANIES,
    TOOL_GET_KEY_DEVELOPMENTS,
    TOOL_GET_KEY_PERSONS,
]
logger.info("📦  Registered tools: %s", _REGISTERED_TOOLS)


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

# app = mcp.http_app()

# # 2. Add the CORS middleware to that app
# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],  # Use ["https://inspector.modelcontextprotocol.io"] for better security
#     allow_credentials=True,
#     allow_methods=["GET", "POST", "OPTIONS"],
#     allow_headers=["*"],
# )
 
 
# # ---------------------------------------------------------------------------
# # Entry point
# # ---------------------------------------------------------------------------
# if __name__ == "__main__":
#     uvicorn.run(
#         "main:app",
#         host=settings.mcp_server_host,
#         port=settings.mcp_server_port,
#         log_level=settings.mcp_log_level.lower(),
#         # Single worker: FastMCP uses in-process ContextVars and singleton
#         # service objects that must not be forked across OS processes.
#         # Scale horizontally by running multiple single-worker instances
#         # behind a load balancer instead.
#         workers=1,
#     )