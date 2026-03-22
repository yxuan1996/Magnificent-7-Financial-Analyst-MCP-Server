"""
auth.py
-------
FastMCP v3 middleware that enforces the full auth flow on every tool call:

    HTTP request arrives
        → JWTHttpMiddleware (Starlette HTTP level)
            → Extracts Bearer token from Authorization header (reliable)
            → Stores raw token in _bearer_token_var ContextVar
        → AuthMiddleware.on_call_tool (MCP protocol level)
            → Reads token from _bearer_token_var
            → Supabase: verify JWT signature + expiry
            → Supabase RBAC: check tool permission (role_permissions table)
            → Supabase RBAC: resolve allowed tickers (role name convention)
            → UserContext stored in _current_user_var ContextVar
        → Tool executes, reads UserContext via get_current_user()

Why two middleware layers?
~~~~~~~~~~~~~~~~~~~~~~~~~~
FastMCP v3 MCP-level middleware (on_call_tool) has indirect access to
HTTP headers via ``context.context.get_http_request()``.  In practice
this is unreliable behind reverse proxies (Prefect Horizon, nginx, etc.)
because the FastMCP Context may not have the HTTP request threaded through
correctly depending on the version and proxy configuration.

JWTHttpMiddleware runs at the Starlette ASGI layer — it always has the
raw HTTP request with all its original headers, regardless of what sits
in front of the server.  The extracted token is placed in a ContextVar
that is visible throughout the entire async call chain (same task).

Middleware hooks
~~~~~~~~~~~~~~~~
- ``JWTHttpMiddleware``    — HTTP-level token extraction (Starlette)
- ``AuthMiddleware``       — MCP-level JWT verification + RBAC (FastMCP)
- ``on_list_tools``        — intentionally omitted; tool listing is open
"""

import logging
from contextvars import ContextVar
from typing import Optional

from fastmcp.server.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from services.auth_service import get_auth_service

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ContextVars — set at HTTP layer, read at MCP layer (same async task)
# ---------------------------------------------------------------------------

# Stores the raw Bearer token string extracted from the HTTP Authorization header.
# Set by JWTHttpMiddleware, read by AuthMiddleware.on_call_tool.
_bearer_token_var: ContextVar[Optional[str]] = ContextVar(
    "_bearer_token_var", default=None
)

# Stores the fully resolved UserContext after JWT + RBAC checks pass.
# Set by AuthMiddleware.on_call_tool, read by tool handlers via get_current_user().
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
# JWTHttpMiddleware — Starlette HTTP layer (reliable header extraction)
# ---------------------------------------------------------------------------

class JWTHttpMiddleware(BaseHTTPMiddleware):
    """
    Starlette BaseHTTPMiddleware that extracts the Bearer token from the
    HTTP Authorization header and stores it in ``_bearer_token_var``.

    This middleware runs at the raw HTTP request level — before FastMCP
    parses the MCP protocol message — so it has guaranteed access to the
    original request headers regardless of proxy configuration.

    The token is stored in a ContextVar rather than on request.state so
    that it is visible to the MCP-level middleware (AuthMiddleware) which
    runs deeper in the call chain within the same async task.
    """

    async def dispatch(self, request: StarletteRequest, call_next):
        # Extract Bearer token from the Authorization header.
        token: Optional[str] = None
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[len("bearer "):].strip()

        if token:
            logger.debug(
                "JWTHttpMiddleware: Bearer token found for %s %s",
                request.method,
                request.url.path,
            )
        else:
            logger.debug(
                "JWTHttpMiddleware: No Bearer token on %s %s",
                request.method,
                request.url.path,
            )

        # Store in ContextVar for the duration of this request.
        # The ContextVar is visible to all awaited code in this async task,
        # including FastMCP's MCP protocol handler and our AuthMiddleware.
        ctx_token = _bearer_token_var.set(token)
        try:
            return await call_next(request)
        finally:
            _bearer_token_var.reset(ctx_token)


# ---------------------------------------------------------------------------
# FastMCP v3 MCP-level Middleware
# ---------------------------------------------------------------------------

class AuthMiddleware(Middleware):
    """
    MCP protocol-level middleware that enforces JWT authentication and
    Supabase RBAC authorisation on every tool call.

    Token acquisition
    ~~~~~~~~~~~~~~~~~
    Reads the raw Bearer token from ``_bearer_token_var``, which was set
    by ``JWTHttpMiddleware`` at the HTTP layer earlier in the same request.
    This is reliable across all proxy/deployment configurations.

    As a safety net, it also tries ``context.context.get_http_request()``
    (the FastMCP native approach) in case JWTHttpMiddleware is not mounted.

    Auth steps
    ~~~~~~~~~~
    1. Verify Supabase JWT signature + expiry.
    2. Check role_permissions: does this user's role grant the tool?
    3. Derive allowed tickers from role names (all_access, Apple_only, …).
    4. Store UserContext in ``_current_user_var`` for tool handlers.

    on_list_tools is intentionally not defined — tool discovery is open.
    """

    async def on_call_tool(self, context, call_next):
        tool_name: str = context.tool_name
        fastmcp_ctx = context.context

        auth_svc = get_auth_service()

        # ── Step 1: Get the Bearer token ──────────────────────────────
        # Primary: read from ContextVar set by JWTHttpMiddleware.
        token: Optional[str] = _bearer_token_var.get()

        # Safety net: try FastMCP's native HTTP request accessor.
        # Silently ignored if it fails (proxy stripped headers, etc.).
        if not token:
            try:
                get_http_req = getattr(fastmcp_ctx, "get_http_request", None)
                if callable(get_http_req):
                    http_req = get_http_req()
                    if http_req is not None:
                        header = http_req.headers.get("authorization", "")
                        if header.lower().startswith("bearer "):
                            token = header[len("bearer "):].strip()
            except Exception as exc:
                logger.debug("FastMCP get_http_request() fallback failed: %s", exc)

        if not token:
            raise PermissionError(
                "Missing Bearer token. "
                "Include 'Authorization: Bearer <jwt>' in every MCP request."
            )

        # ── Step 2: Verify the JWT ────────────────────────────────────
        try:
            payload = auth_svc.verify_token(token)
        except PermissionError:
            raise
        except Exception as exc:
            raise PermissionError(f"Token verification failed: {exc}") from exc

        user_id: str = payload.get("sub", "")
        if not user_id:
            raise PermissionError("JWT is missing the required 'sub' claim.")

        # ── Step 3: Tool-level RBAC ───────────────────────────────────
        if not auth_svc.check_tool_access(user_id, tool_name):
            raise PermissionError(
                f"Permission denied: your account is not authorised to call "
                f"'{tool_name}'. Ask an administrator to update your role "
                f"permissions in Supabase."
            )

        # ── Step 4: Ticker-level RBAC ─────────────────────────────────
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

        # ── Propagate UserContext, call tool, then reset ──────────────
        ctx_token = _current_user_var.set(user_ctx)
        try:
            return await call_next(context)
        finally:
            _current_user_var.reset(ctx_token)

    # on_list_tools is intentionally not defined here.
    #
    # FastMCP middleware only intercepts hooks that are explicitly implemented.
    # Omitting on_list_tools means tool discovery is open to any caller —
    # no Bearer token required.  Authentication is enforced on every actual
    # tool *call* via on_call_tool above.