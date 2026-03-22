"""
people_tools.py
---------------
MCP tools that surface **key persons** from the Neo4j knowledge graph.

Graph schema used
~~~~~~~~~~~~~~~~~
  (:Document)-[:BELONGS_TO]->(:Company)
  (:Document)-[:MENTIONS]->(:key_person)

Tools
~~~~~
- get_key_persons → filtered by ticker and optional role
"""

import logging
from enum import Enum
from typing import Optional

from fastmcp import FastMCP

# from auth import get_current_user
from services.neo4j_service import get_neo4j_service

logger = logging.getLogger(__name__)
 
TOOL_GET_KEY_PERSONS = "get_key_persons"


class ExecutiveRole(str, Enum):
    """Standardised leadership roles extracted from annual report filings."""
    CEO          = "CEO"
    CFO          = "CFO"
    COO          = "COO"
    CHAIRPERSON  = "Chairperson"
    BOARD_MEMBER = "BoardMember"


def register_people_tools(mcp: FastMCP) -> None:
    """Register all key-person tools onto *mcp*."""

    @mcp.tool(name="get_key_persons")
    async def get_key_persons(
        ticker: str,
        role: Optional[ExecutiveRole] = None,
    ) -> dict:
        """
        Retrieve key executives and board members mentioned in a company's
        annual report filings (Form 10-K), stored in Neo4j.

        Parameters
        ----------
        ticker : str
            Company ticker symbol, e.g. ``"META"``.
        role : ExecutiveRole, optional
            Filter by leadership role. One of:

            - ``CEO``         - Chief Executive Officer
            - ``CFO``         - Chief Financial Officer
            - ``COO``         - Chief Operating Officer
            - ``Chairperson`` - Board chair or executive chair
            - ``BoardMember`` - Non-executive board member

            If omitted, all key persons are returned regardless of role.

        Returns
        -------
        dict with keys:
            ticker, role_filter, person_count, persons (list)

        Each person item contains:
            name, role, description, ticker, company_name
        """
        # user = get_current_user()
        # user.assert_tickers([ticker])

        svc = get_neo4j_service()
        results = svc.get_key_persons(
            ticker=ticker.upper(),
            role=role.value if role else None,
        )

        return {
            "ticker": ticker.upper(),
            "role_filter": role.value if role else None,
            "person_count": len(results),
            "persons": results,
        }