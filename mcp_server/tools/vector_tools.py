"""
vector_tools.py
---------------
MCP tools that perform semantic search over the Pinecone vector index.

Tools
~~~~~
- search_report_text   → paragraph text chunks from annual reports
- search_report_tables → table chunks (returned as Markdown)
"""

import logging
from typing import Optional

from fastmcp import FastMCP

from auth import get_current_user
from services.pinecone_service import get_pinecone_service
from services.auth_service import MAG7_TICKERS

logger = logging.getLogger(__name__)

TOOL_SEARCH_TEXT   = "search_report_text"
TOOL_SEARCH_TABLES = "search_report_tables"


def register_vector_tools(mcp: FastMCP) -> None:
    """Register all vector search tools onto *mcp*."""

    @mcp.tool(name=TOOL_SEARCH_TEXT)
    async def search_report_text(
        query: str,
        tickers: Optional[list[str]] = None,
        top_k: int = 5,
        fiscal_year: Optional[int] = None,
    ) -> dict:
        """
        Semantic search over **paragraph text** chunks from Magnificent 7
        annual reports stored in Pinecone.

        Parameters
        ----------
        query : str
            Natural-language question or keyword phrase to search for.
        tickers : list[str], optional
            Restrict results to specific company tickers.
            Defaults to all companies the user is allowed to access.
            Valid values: AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA.
        top_k : int
            Number of results to return (1-20). Default 5.
        fiscal_year : int, optional
            Restrict results to a specific fiscal year (e.g. 2023).

        Returns
        -------
        dict with keys:
            results : list of hits, each containing:
                - ticker, fiscal_year, section, page, text, score
        """
        user = get_current_user()

        requested = [t.upper() for t in tickers] if tickers else MAG7_TICKERS
        user.assert_tickers(requested)

        top_k = max(1, min(top_k, 20))
        svc = get_pinecone_service()

        hits = svc.search_report_text(
            query=query,
            tickers=user.filter_tickers(requested),
            top_k=top_k,
            fiscal_year=fiscal_year,
        )

        return {
            "query": query,
            "tickers": requested,
            "fiscal_year": fiscal_year,
            "result_count": len(hits),
            "results": hits,
        }

    @mcp.tool(name=TOOL_SEARCH_TABLES)
    async def search_report_tables(
        query: str,
        tickers: Optional[list[str]] = None,
        top_k: int = 5,
        fiscal_year: Optional[int] = None,
    ) -> dict:
        """
        Semantic search over **table** chunks (Markdown format) from
        Magnificent 7 annual reports stored in Pinecone.

        Parameters
        ----------
        query : str
            Natural-language question or keyword phrase (e.g. "revenue by
            segment", "operating expenses 2023").
        tickers : list[str], optional
            Restrict results to specific company tickers.
            Defaults to all companies the user is allowed to access.
        top_k : int
            Number of results to return (1-20). Default 5.
        fiscal_year : int, optional
            Restrict results to a specific fiscal year.

        Returns
        -------
        dict with keys:
            results : list of hits, each containing:
                - ticker, fiscal_year, section, page, table_markdown, score
        """
        user = get_current_user()

        requested = [t.upper() for t in tickers] if tickers else MAG7_TICKERS
        user.assert_tickers(requested)

        top_k = max(1, min(top_k, 20))
        svc = get_pinecone_service()

        hits = svc.search_report_tables(
            query=query,
            tickers=user.filter_tickers(requested),
            top_k=top_k,
            fiscal_year=fiscal_year,
        )

        return {
            "query": query,
            "tickers": requested,
            "fiscal_year": fiscal_year,
            "result_count": len(hits),
            "results": hits,
        }