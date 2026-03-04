"""
auth.py
-------
Starlette middleware that validates the Bearer JWT on every HTTP request
and populates ``request.state.user_id`` + ``request.state.allowed_tickers``.

Tools retrieve the user context via ``get_user_context(ctx)``, where *ctx*
is the FastMCP ``Context`` object injected into each tool function.
"""

import logging
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from mcp.server.fastmcp import Context

from services.auth_service import get_auth_service

logger = logging.getLogger(__name__)

# Header the client must send: "Authorization: Bearer <jwt>"
AUTH_HEADER = "authorization"

# Paths that bypass auth (e.g. health-check)
PUBLIC_PATHS: set[str] = {"/health", "/"}


class JWTAuthMiddleware(BaseHTTPMiddleware):
    """
    Validates Supabase JWTs on inbound requests.

    On success  → sets ``request.state.user_id`` and clears the way.
    On failure  → returns 401 JSON immediately.
    Public paths (``/health``) are skipped.
    """

    async def dispatch(self, request: Request, call_next):
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        auth_header: Optional[str] = request.headers.get(AUTH_HEADER)
        if not auth_header or not auth_header.lower().startswith("bearer "):
            return JSONResponse(
                {"error": "Missing or malformed Authorization header."},
                status_code=401,
            )

        token = auth_header[len("bearer "):].strip()
        auth = get_auth_service()

        try:
            payload = auth.verify_token(token)
        except PermissionError as exc:
            logger.warning("JWT verification failed: %s", exc)
            return JSONResponse({"error": str(exc)}, status_code=401)

        user_id: str = payload.get("sub", "")
        if not user_id:
            return JSONResponse({"error": "Token missing 'sub' claim."}, status_code=401)

        # Attach user context to request state for downstream tool calls.
        request.state.user_id = user_id
        request.state.allowed_tickers = auth.get_allowed_tickers(user_id)

        return await call_next(request)


# ---------------------------------------------------------------------------
# Helpers used inside tool functions
# ---------------------------------------------------------------------------

class UserContext:
    """Thin wrapper around per-request auth state passed to tools."""

    def __init__(self, user_id: str, allowed_tickers: list[str]) -> None:
        self.user_id = user_id
        self.allowed_tickers = allowed_tickers

    def filter_tickers(self, requested: list[str]) -> list[str]:
        allowed = set(t.upper() for t in self.allowed_tickers)
        return [t for t in requested if t.upper() in allowed]

    def assert_tickers(self, requested: list[str]) -> None:
        forbidden = [t for t in requested if t.upper() not in {x.upper() for x in self.allowed_tickers}]
        if forbidden:
            raise PermissionError(
                f"Access denied for ticker(s): {', '.join(forbidden)}. "
                "Your account does not have permission to view this data."
            )

    def assert_tool(self, tool_name: str) -> None:
        auth = get_auth_service()
        auth.assert_tool_access(self.user_id, tool_name)


def get_user_context(ctx: Context) -> UserContext:
    """
    Extract the authenticated ``UserContext`` from the FastMCP request context.

    FastMCP exposes the raw Starlette ``Request`` via ``ctx.request``.
    Raises ``PermissionError`` if no auth state is found (should not happen
    if the middleware is correctly wired).
    """
    request: Optional[Request] = getattr(ctx, "request", None)
    if request is None:
        raise PermissionError("No HTTP request context available.")

    user_id: Optional[str] = getattr(request.state, "user_id", None)
    allowed_tickers: Optional[list[str]] = getattr(request.state, "allowed_tickers", None)

    if user_id is None:
        raise PermissionError("Request is not authenticated.")

    return UserContext(user_id=user_id, allowed_tickers=allowed_tickers or [])