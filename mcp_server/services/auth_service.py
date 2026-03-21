"""
auth_service.py
---------------
Single service responsible for BOTH authentication and authorization,
backed entirely by Supabase.

Authentication
~~~~~~~~~~~~~~
  Verify the Supabase-issued JWT (HS256, audience="authenticated").
  Extracts the user's UUID from the ``sub`` claim.

Authorization
~~~~~~~~~~~~~
  Queries the three Supabase RBAC tables to decide:

    1. Tool access   — does any of the user's roles grant this tool_name
                       in role_permissions?
    2. Ticker access — which MAG7 tickers does the user's role set cover?

  RBAC schema (tables already exist in Supabase):

    roles            (id uuid PK, name text UNIQUE)
    user_roles       (user_id → auth.users.id, role_id → roles.id)
    role_permissions (role_id → roles.id, tool_name text)

  Ticker-access logic
  ~~~~~~~~~~~~~~~~~~~
  Role name conventions map directly to ticker access:

    all_access        → every MAG7 ticker
    Apple_only        → AAPL
    Microsoft_only    → MSFT
    Google_only       → GOOGL
    Amazon_only       → AMZN
    Nvidia_only       → NVDA
    Meta_only         → META
    Tesla_only        → TSLA

  A user may hold multiple roles.  Their allowed-ticker set is the
  union of tickers granted by each role they hold.

Caching
~~~~~~~
  Both ``check_tool_access`` and ``get_allowed_tickers`` results are
  cached per user_id with a 5-minute TTL (``cachetools.TTLCache``) so
  that Supabase is not queried on every single tool invocation.
  Call ``invalidate_cache(user_id)`` after any role change.
"""

import logging
from typing import Optional

import jwt
from cachetools import TTLCache
from supabase import create_client, Client

from mcp_server.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MAG7 constants  (shared across the codebase via this module)
# ---------------------------------------------------------------------------

MAG7_TICKERS: list[str] = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"]

# Maps each ticker to the Supabase role name that grants access to it.
TICKER_ROLE_MAP: dict[str, str] = {
    "AAPL":  "Apple_only",
    "MSFT":  "Microsoft_only",
    "GOOGL": "Google_only",
    "AMZN":  "Amazon_only",
    "NVDA":  "Nvidia_only",
    "META":  "Meta_only",
    "TSLA":  "Tesla_only",
}

ALL_ACCESS_ROLE = "all_access"

# Cache TTL in seconds (5 minutes)
_CACHE_TTL = 300
_CACHE_SIZE = 1024


class AuthService:
    """
    Handles JWT verification and Supabase RBAC-based authorization.

    One singleton instance is created at startup (see ``get_auth_service()``).
    Results for tool-access and ticker-access checks are cached per user_id
    to avoid redundant Supabase round-trips within the same cache window.
    """

    def __init__(self) -> None:
        self._supabase: Client = create_client(
            settings.supabase_url,
            settings.supabase_service_role_key,  # service role for RBAC reads
        )
        self._jwt_secret: str = settings.supabase_jwt_secret

        # Per-user caches  {user_id: result}
        self._tool_cache:   TTLCache = TTLCache(maxsize=_CACHE_SIZE, ttl=_CACHE_TTL)
        self._ticker_cache: TTLCache = TTLCache(maxsize=_CACHE_SIZE, ttl=_CACHE_TTL)

    # ------------------------------------------------------------------
    # Authentication — JWT verification
    # ------------------------------------------------------------------

    def verify_token(self, token: str) -> dict:
        """
        Decode and verify a Supabase-issued JWT.

        Returns the decoded payload on success. Key claims:
            sub   — user UUID (primary key in auth.users)
            email — user email address
            exp   — expiry Unix timestamp

        Raises ``PermissionError`` on expiry, bad signature, or any other
        token-level failure.
        """
        try:
            payload = jwt.decode(
                token,
                self._jwt_secret,
                algorithms=["HS256"],
                audience="authenticated",
            )
            logger.debug("JWT verified | user=%s", payload.get("sub"))
            return payload
        except jwt.ExpiredSignatureError:
            raise PermissionError("Token has expired. Please log in again.")
        except jwt.InvalidTokenError as exc:
            raise PermissionError(f"Invalid token: {exc}")

    # ------------------------------------------------------------------
    # Authorization — RBAC queries
    # ------------------------------------------------------------------

    def get_user_roles(self, user_id: str) -> list[str]:
        """
        Return the list of role *names* assigned to *user_id*.

        Executes:
            SELECT roles.name
            FROM user_roles
            JOIN roles ON user_roles.role_id = roles.id
            WHERE user_roles.user_id = :user_id
        """
        result = (
            self._supabase
            .table("user_roles")
            .select("roles(name)")
            .eq("user_id", user_id)
            .execute()
        )
        role_names: list[str] = []
        for row in result.data or []:
            role_obj = row.get("roles")
            if isinstance(role_obj, dict) and role_obj.get("name"):
                role_names.append(role_obj["name"])
            elif isinstance(role_obj, list):
                # Supabase may return a list when the join key is non-unique
                for r in role_obj:
                    if r.get("name"):
                        role_names.append(r["name"])
        logger.debug("RBAC | user=%s roles=%s", user_id, role_names)
        return role_names

    def check_tool_access(self, user_id: str, tool_name: str) -> bool:
        """
        Return ``True`` if *user_id* holds any role that grants *tool_name*.

        Checks the ``role_permissions`` table: a user may call a tool if
        at least one of their roles has a matching (role_id, tool_name) row.

        Results are cached per ``(user_id, tool_name)`` for 5 minutes.
        """
        cache_key = f"{user_id}:{tool_name}"
        if cache_key in self._tool_cache:
            return self._tool_cache[cache_key]

        result = (
            self._supabase
            .table("user_roles")
            .select("roles(role_permissions(tool_name))")
            .eq("user_id", user_id)
            .execute()
        )

        allowed = False
        for row in result.data or []:
            role_obj = row.get("roles") or {}
            # Normalise: Supabase may return a dict or a list
            role_list = role_obj if isinstance(role_obj, list) else [role_obj]
            for role in role_list:
                for perm in role.get("role_permissions") or []:
                    if perm.get("tool_name") == tool_name:
                        allowed = True
                        break
                if allowed:
                    break
            if allowed:
                break

        logger.debug(
            "RBAC | user=%s tool=%s allowed=%s", user_id, tool_name, allowed
        )
        self._tool_cache[cache_key] = allowed
        return allowed

    def get_allowed_tickers(self, user_id: str) -> list[str]:
        """
        Return the list of MAG7 tickers the user may access.

        Derives ticker access from role *names* (not from a separate table):

        - ``all_access``     → all 7 tickers
        - ``Apple_only``     → AAPL
        - ``Microsoft_only`` → MSFT
        - … (see TICKER_ROLE_MAP)

        A user holding multiple roles gets the union of all covered tickers.
        Results are cached per user_id for 5 minutes.
        """
        if user_id in self._ticker_cache:
            return self._ticker_cache[user_id]

        roles = self.get_user_roles(user_id)

        if ALL_ACCESS_ROLE in roles:
            tickers = list(MAG7_TICKERS)
        else:
            tickers = [
                ticker
                for ticker, role_name in TICKER_ROLE_MAP.items()
                if role_name in roles
            ]

        logger.debug("RBAC | user=%s allowed_tickers=%s", user_id, tickers)
        self._ticker_cache[user_id] = tickers
        return tickers

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def invalidate_cache(self, user_id: str) -> None:
        """
        Evict all cached RBAC results for *user_id*.

        Call this after any role or permission change for the user so that
        the next request re-queries Supabase for fresh data.
        """
        # Remove tool-access entries for this user (keys are "user_id:tool_name")
        stale_keys = [k for k in self._tool_cache if k.startswith(f"{user_id}:")]
        for k in stale_keys:
            self._tool_cache.pop(k, None)
        self._ticker_cache.pop(user_id, None)
        logger.info("Cache invalidated for user %s", user_id)

    # ------------------------------------------------------------------
    # Raw Supabase client (available for admin queries if needed)
    # ------------------------------------------------------------------

    @property
    def supabase(self) -> Client:
        return self._supabase


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_auth_service: Optional[AuthService] = None


def get_auth_service() -> AuthService:
    global _auth_service
    if _auth_service is None:
        _auth_service = AuthService()
    return _auth_service