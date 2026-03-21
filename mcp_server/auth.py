"""
auth.py
-------
FastMCP v3 middleware that enforces the full auth flow on every tool call:

    Supabase JWT verification
        → Supabase RBAC: check tool permission (role_permissions table)
        → Supabase RBAC: resolve allowed tickers (role name convention)
        → UserContext stored in ContextVar
        → Tool executes

Architecture
~~~~~~~~~~~~
FastMCP v3 middleware operates at the MCP protocol level, not the HTTP level.
Python's ``contextvars`` carry the verified ``UserContext`` through the async
call chain so tools call ``get_current_user()`` with no extra parameters.

Both authorization checks delegate entirely to ``AuthService`` which queries
the three Supabase RBAC tables and caches results with a 5-minute TTL.

Middleware hooks
~~~~~~~~~~~~~~~~
- ``on_call_tool``  — full authn + authz guard
- ``on_list_tools`` — JWT identity check only (no data exposure)
"""

import logging
from contextvars import ContextVar
from typing import Optional

from fastmcp.server.middleware import Middleware
from services.auth_service import get_auth_service

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-request ContextVar
# ---------------------------------------------------------------------------
# Set by AuthMiddleware.on_call_tool before calling the next handler.
# Reset in a ``finally`` block — never leaks between concurrent requests.
_current_user_var: ContextVar[Optional["UserContext"]] = ContextVar(
    "_current_user_var", default=None
)


# ---------------------------------------------------------------------------
# UserContext
# ---------------------------------------------------------------------------

class UserContext:
    """
    Carries the authenticated user's identity and resolved data-access scope.

    Populated once by ``AuthMiddleware`` and consumed by tool handlers via
    ``get_current_user()``.
    """

    def __init__(self, user_id: str, allowed_tickers: list[str]) -> None:
        self.user_id = user_id
        self.allowed_tickers = [t.upper() for t in allowed_tickers]

    def filter_tickers(self, requested: list[str]) -> list[str]:
        """Return only the subset of *requested* tickers this user may access."""
        allowed = set(self.allowed_tickers)
        return [t for t in requested if t.upper() in allowed]

    def assert_tickers(self, requested: list[str]) -> None:
        """Raise ``PermissionError`` if any requested ticker is not in scope."""
        forbidden = [
            t for t in requested if t.upper() not in set(self.allowed_tickers)
        ]
        if forbidden:
            raise PermissionError(
                f"Access denied for ticker(s): {', '.join(forbidden)}. "
                "Your account does not have permission to view this data."
            )

    def __repr__(self) -> str:  # pragma: no cover
        return f"UserContext(user_id={self.user_id!r}, tickers={self.allowed_tickers})"


# ---------------------------------------------------------------------------
# Public helper used by tool handlers
# ---------------------------------------------------------------------------

def get_current_user() -> UserContext:
    """
    Return the ``UserContext`` for the currently executing tool call.

    Safe to call from any async tool function — the value is set by
    ``AuthMiddleware`` before the tool handler runs and is scoped to the
    current async task via ``ContextVar``.

    Raises ``PermissionError`` if called outside an authenticated context.
    """
    user = _current_user_var.get()
    if user is None:
        raise PermissionError(
            "No authenticated user in context. "
            "Ensure the MCP server is running with AuthMiddleware attached."
        )
    return user


# ---------------------------------------------------------------------------
# Token extraction (HTTP transport compatible)
# ---------------------------------------------------------------------------

def _extract_bearer_token(fastmcp_ctx) -> Optional[str]:
    """
    Pull the raw JWT string out of the ``Authorization: Bearer <token>`` header.

    Tries two access patterns to stay compatible across FastMCP minor releases:
      1. ``ctx.get_http_request()``  — explicit API (FastMCP v3+)
      2. ``ctx.request``             — legacy attribute fallback

    Returns ``None`` if no Bearer token can be found.
    """
    # Pattern 1 — preferred (FastMCP v3+)
    get_http_req = getattr(fastmcp_ctx, "get_http_request", None)
    if callable(get_http_req):
        try:
            http_req = get_http_req()
            if http_req is not None:
                header = http_req.headers.get("authorization", "")
                if header.lower().startswith("bearer "):
                    return header[len("bearer "):].strip()
        except Exception:
            pass

    # Pattern 2 — legacy fallback
    http_req = getattr(fastmcp_ctx, "request", None)
    if http_req is not None:
        header = getattr(http_req, "headers", {}).get("authorization", "")
        if header.lower().startswith("bearer "):
            return header[len("bearer "):].strip()

    return None


# ---------------------------------------------------------------------------
# FastMCP v3 Middleware
# ---------------------------------------------------------------------------

class AuthMiddleware(Middleware):
    """
    Single-service auth middleware backed entirely by Supabase.

    Step 1 — Authentication
        Verifies the Supabase JWT (HS256 signature, expiry, audience claim).
        Extracts the user UUID from the ``sub`` claim.

    Step 2 — Tool authorization
        Queries ``role_permissions`` via ``AuthService.check_tool_access()``:
        the user must hold at least one role that grants the requested tool.

    Step 3 — Ticker authorization
        Derives allowed tickers from role names via
        ``AuthService.get_allowed_tickers()``:
          - ``all_access``     → all MAG7 tickers
          - ``Apple_only``     → AAPL only
          - ``Microsoft_only`` → MSFT only
          - … (union of all roles the user holds)

    Both steps 2 and 3 are backed by a 5-minute TTL cache in AuthService
    to avoid redundant Supabase queries within the same session window.

    The resolved ``UserContext`` is stored in a ``ContextVar`` so tool
    handlers can call ``get_current_user()`` directly.
    """

    # ------------------------------------------------------------------
    # on_call_tool — full auth + authz gate
    # ------------------------------------------------------------------

    async def on_call_tool(self, context, call_next):
        """
        Intercepts every tool invocation.

        ``context`` attributes:
            context.tool_name  — str, name of the tool being called
            context.arguments  — dict, raw tool arguments
            context.context    — FastMCP Context object
        """
        tool_name: str = context.tool_name
        fastmcp_ctx = context.context

        auth_svc = get_auth_service()

        # ── Step 1: JWT verification ─────────────────────────────────
        token = _extract_bearer_token(fastmcp_ctx)
        if not token:
            raise PermissionError(
                "Missing Bearer token. "
                "Include 'Authorization: Bearer <jwt>' in every request."
            )

        try:
            payload = auth_svc.verify_token(token)
        except PermissionError:
            raise
        except Exception as exc:
            raise PermissionError(f"Token verification failed: {exc}") from exc

        user_id: str = payload.get("sub", "")
        if not user_id:
            raise PermissionError("JWT is missing the required 'sub' claim.")

        # ── Step 2: Tool-level authorization ─────────────────────────
        # Check role_permissions table: does this user's role set include
        # a row granting access to tool_name?
        if not auth_svc.check_tool_access(user_id, tool_name):
            raise PermissionError(
                f"Permission denied: your account is not authorised to "
                f"call '{tool_name}'. Ask an administrator to update your "
                f"role permissions in Supabase."
            )

        # ── Step 3: Ticker-level authorization ───────────────────────
        # Derive allowed tickers from role names (all_access, Apple_only, …)
        allowed_tickers = auth_svc.get_allowed_tickers(user_id)
        if not allowed_tickers:
            raise PermissionError(
                "Permission denied: your account has no company data access. "
                "Assign a role such as 'all_access' or 'Apple_only' in Supabase."
            )

        user_ctx = UserContext(user_id=user_id, allowed_tickers=allowed_tickers)
        logger.info(
            "auth | user=%s tool=%s tickers=%s",
            user_id, tool_name, allowed_tickers,
        )

        # ── Store in ContextVar, call tool, then reset ────────────────
        token_var = _current_user_var.set(user_ctx)
        try:
            return await call_next(context)
        finally:
            _current_user_var.reset(token_var)

    # ------------------------------------------------------------------
    # on_list_tools — identity check only
    # ------------------------------------------------------------------

    async def on_list_tools(self, context, call_next):
        """
        Require a valid JWT before the tool list is returned to the client.

        No RBAC queries are made here — listing tools does not expose data,
        but we still require proof of identity to prevent information leakage
        about the server's capabilities to unauthenticated callers.
        """
        fastmcp_ctx = getattr(context, "context", context)

        token = _extract_bearer_token(fastmcp_ctx)
        if not token:
            raise PermissionError(
                "A valid Bearer token is required to list tools."
            )

        auth_svc = get_auth_service()
        try:
            auth_svc.verify_token(token)
        except PermissionError:
            raise
        except Exception as exc:
            raise PermissionError(f"Token verification failed: {exc}") from exc

        return await call_next(context)