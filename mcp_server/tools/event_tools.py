"""
event_tools.py
--------------
MCP tools that surface **key developments** from the Neo4j knowledge graph.

Graph schema used
~~~~~~~~~~~~~~~~~
  (:Document)-[:BELONGS_TO]->(:Company)
  (:Document)-[:BELONGS_TO]->(:FiscalYear)
  (:Document)-[:MENTIONS]->(:key_development)

Tools
~~~~~
- get_key_developments → filtered by ticker, category and/or fiscal year
"""

import logging
from enum import Enum
from typing import Optional

from fastmcp import FastMCP

from auth import get_current_user
from services.neo4j_service import get_neo4j_service

logger = logging.getLogger(__name__)

 
TOOL_GET_KEY_DEVELOPMENTS = "get_key_developments"


class DevelopmentCategory(str, Enum):
    """Allowed categories for key developments extracted from 10-K filings."""
    M_AND_A           = "M&A"
    RESTRUCTURING     = "Restructuring"
    LITIGATION        = "Litigation"
    PRODUCT_LAUNCH    = "ProductLaunch"
    REGULATORY_ACTION = "RegulatoryAction"
    GUIDANCE_CHANGE   = "GuidanceChange"


def register_event_tools(mcp: FastMCP) -> None:
    """Register all key development tools onto *mcp*."""

    @mcp.tool(name="get_key_developments")
    async def get_key_developments(
        ticker: str,
        category: Optional[DevelopmentCategory] = None,
        fiscal_year: Optional[int] = None,
    ) -> dict:
        """
        Retrieve key corporate developments for a company as mentioned in
        its annual report filings (Form 10-K), stored in Neo4j.

        Parameters
        ----------
        ticker : str
            Company ticker symbol, e.g. ``"NVDA"``.
        category : DevelopmentCategory, optional
            Filter by development type. One of:

            - ``M&A``              - Mergers, acquisitions, divestitures
            - ``Restructuring``    - Layoffs, reorganisations, facility closures
            - ``Litigation``       - Lawsuits, settlements, regulatory fines
            - ``ProductLaunch``    - New products, services, or platform releases
            - ``RegulatoryAction`` - Government investigations, consent decrees
            - ``GuidanceChange``   - Forward guidance revisions (up or down)

        fiscal_year : int, optional
            Filter to a specific four-digit fiscal year (e.g. 2023).

        Returns
        -------
        dict with keys:
            ticker, category_filter, fiscal_year_filter,
            development_count, developments (list)

        Each development item contains:
            title, category, description, date, fiscal_year,
            ticker, company_name
        """
        user = get_current_user()
        user.assert_tickers([ticker])

        svc = get_neo4j_service()
        results = svc.get_key_developments(
            ticker=ticker.upper(),
            category=category.value if category else None,
            fiscal_year=fiscal_year,
        )

        return {
            "ticker": ticker.upper(),
            "category_filter": category.value if category else None,
            "fiscal_year_filter": fiscal_year,
            "development_count": len(results),
            "developments": results,
        }