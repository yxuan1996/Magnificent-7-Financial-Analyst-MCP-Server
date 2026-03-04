"""
auth_service.py
---------------
Handles JWT verification via Supabase and RBAC permission lookups.
Caches permission sets per user to reduce DB round-trips.
"""

import logging
from functools import lru_cache
from typing import Optional

import jwt
from supabase import create_client, Client

from config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Magnificent 7 ticker → human-readable name mapping
# ---------------------------------------------------------------------------
MAG7_TICKERS: list[str] = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"]

TICKER_ROLE_MAP: dict[str, str] = {
    "AAPL": "Apple_only",
    "MSFT": "Microsoft_only",
    "GOOGL": "Google_only",
    "AMZN": "Amazon_only",
    "NVDA": "Nvidia_only",
    "META": "Meta_only",
    "TSLA": "Tesla_only",
}

ALL_ACCESS_ROLE = "all_access"


class AuthService:
    """Verifies Supabase JWTs and resolves per-user RBAC permissions."""

    def __init__(self) -> None:
        self._supabase: Client = create_client(
            settings.supabase_url,
            settings.supabase_service_role_key,   # service role for admin queries
        )
        self._jwt_secret: str = settings.supabase_jwt_secret

    # ------------------------------------------------------------------
    # JWT verification
    # ------------------------------------------------------------------

    def verify_token(self, token: str) -> dict:
        """
        Decode and verify a Supabase-issued JWT.

        Returns the decoded payload (includes ``sub`` = user_id).
        Raises ``jwt.InvalidTokenError`` on failure.
        """
        try:
            payload = jwt.decode(
                token,
                self._jwt_secret,
                algorithms=["HS256"],
                audience="authenticated",
            )
            return payload
        except jwt.ExpiredSignatureError:
            raise PermissionError("Token has expired.")
        except jwt.InvalidTokenError as exc:
            raise PermissionError(f"Invalid token: {exc}")

    # ------------------------------------------------------------------
    # RBAC helpers
    # ------------------------------------------------------------------

    def get_user_roles(self, user_id: str) -> list[str]:
        """Return list of role names assigned to *user_id*."""
        result = (
            self._supabase.table("user_roles")
            .select("roles(name)")
            .eq("user_id", user_id)
            .execute()
        )
        roles: list[str] = []
        for row in result.data or []:
            role_obj = row.get("roles")
            if role_obj and role_obj.get("name"):
                roles.append(role_obj["name"])
        logger.debug("User %s has roles: %s", user_id, roles)
        return roles

    def get_tool_permissions(self, user_id: str) -> set[str]:
        """
        Return the set of tool names the user is permitted to call.

        Joins ``user_roles`` → ``role_permissions``.
        """
        result = (
            self._supabase.table("user_roles")
            .select("roles(role_permissions(tool_name))")
            .eq("user_id", user_id)
            .execute()
        )
        tools: set[str] = set()
        for row in result.data or []:
            role_obj = row.get("roles") or {}
            for perm in role_obj.get("role_permissions") or []:
                if perm.get("tool_name"):
                    tools.add(perm["tool_name"])
        return tools

    def get_allowed_tickers(self, user_id: str) -> list[str]:
        """
        Derive which tickers the user may access from their assigned roles.

        Rules:
        - ``all_access``      → every MAG7 ticker
        - ``Apple_only``      → AAPL only
        - ``Microsoft_only``  → MSFT only
        - … (see TICKER_ROLE_MAP)
        - Any unrecognised role grants no additional tickers.
        """
        roles = self.get_user_roles(user_id)
        if ALL_ACCESS_ROLE in roles:
            return list(MAG7_TICKERS)

        allowed: list[str] = []
        for ticker, role_name in TICKER_ROLE_MAP.items():
            if role_name in roles:
                allowed.append(ticker)

        return allowed

    # ------------------------------------------------------------------
    # Combined auth check used by tool wrappers
    # ------------------------------------------------------------------

    def assert_tool_access(
        self,
        user_id: str,
        tool_name: str,
        tickers: Optional[list[str]] = None,
    ) -> None:
        """
        Raises ``PermissionError`` if:
        - The user does not have permission for *tool_name*, OR
        - Any of the requested *tickers* are outside the user's allowed set.
        """
        allowed_tools = self.get_tool_permissions(user_id)
        if tool_name not in allowed_tools:
            raise PermissionError(
                f"Your account does not have permission to use the tool '{tool_name}'."
            )

        if tickers:
            allowed_tickers = self.get_allowed_tickers(user_id)
            forbidden = [t for t in tickers if t.upper() not in allowed_tickers]
            if forbidden:
                raise PermissionError(
                    f"Your account does not have access to data for: {', '.join(forbidden)}."
                )

    def filter_tickers(self, user_id: str, requested: list[str]) -> list[str]:
        """Return only those tickers from *requested* that the user may access."""
        allowed = set(self.get_allowed_tickers(user_id))
        return [t for t in requested if t.upper() in allowed]


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_auth_service: Optional[AuthService] = None


def get_auth_service() -> AuthService:
    global _auth_service
    if _auth_service is None:
        _auth_service = AuthService()
    return _auth_service