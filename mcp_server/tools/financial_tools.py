"""
financial_tools.py
------------------
MCP tools that query **financial facts** from the Neo4j knowledge graph.

Graph schema used
~~~~~~~~~~~~~~~~~
  (:Company)
  (:Document)-[:BELONGS_TO]->(:Company)
  (:Document)-[:BELONGS_TO]->(:FiscalYear)
  (:Document)-[:REPORTS]->(:Fact)-[:FOR_METRIC]->(:Metric)

Tools
~~~~~
- get_financial_metric            → single company, one metric, optional year
- compare_metric_across_years     → single company, one metric, all years
- compare_metric_across_companies → multiple companies, one metric, one year
"""

import logging
from typing import Optional

from fastmcp import FastMCP

# from auth import get_current_user
from services.neo4j_service import get_neo4j_service

logger = logging.getLogger(__name__)

 
TOOL_GET_FINANCIAL_METRIC  = "get_financial_metric"
TOOL_COMPARE_YEARS         = "compare_metric_across_years"
TOOL_COMPARE_COMPANIES     = "compare_metric_across_companies"


def register_financial_tools(mcp: FastMCP) -> None:
    """Register all financial graph tools onto *mcp*."""

    @mcp.tool(name="get_financial_metric")
    async def get_financial_metric(
        ticker: str,
        metric_name: str,
        fiscal_year: Optional[int] = None,
    ) -> dict:
        """
        Retrieve a financial metric value for a single company from
        the Neo4j knowledge graph (sourced from Form 10-K filings).

        Parameters
        ----------
        ticker : str
            Company ticker symbol, e.g. ``"AAPL"``.
        metric_name : str
            Name of the financial metric to look up, e.g.
            ``"Revenue"``, ``"Net Income"``, ``"EPS"``,
            ``"Operating Cash Flow"``, ``"Gross Margin"``.
            Matching is case-insensitive.
        fiscal_year : int, optional
            Four-digit fiscal year (e.g. 2023). If omitted, all
            available years are returned, most recent first.

        Returns
        -------
        dict with keys:
            ticker, metric_name, fiscal_year (if filtered), facts (list)
        """
        # user = get_current_user()
        # user.assert_tickers([ticker])

        svc = get_neo4j_service()
        results = svc.get_financial_metric(
            ticker=ticker.upper(),
            metric_name=metric_name,
            fiscal_year=fiscal_year,
        )

        return {
            "ticker": ticker.upper(),
            "metric_name": metric_name,
            "fiscal_year_filter": fiscal_year,
            "record_count": len(results),
            "facts": results,
        }

    @mcp.tool(name="compare_metric_across_years")
    async def compare_metric_across_years(
        ticker: str,
        metric_name: str,
    ) -> dict:
        """
        Return all available yearly values for a given metric at one company,
        ordered chronologically — useful for trend analysis and time-series charts.

        Parameters
        ----------
        ticker : str
            Company ticker symbol, e.g. ``"MSFT"``.
        metric_name : str
            Financial metric name, e.g. ``"Revenue"``, ``"Free Cash Flow"``.

        Returns
        -------
        dict with keys:
            ticker, metric_name, year_count, time_series (list of {fiscal_year, value, unit})
        """
        # user = get_current_user()
        # user.assert_tickers([ticker])

        svc = get_neo4j_service()
        results = svc.compare_metric_across_years(
            ticker=ticker.upper(),
            metric_name=metric_name,
        )

        time_series = [
            {
                "fiscal_year": r["fiscal_year"],
                "value": r["value"],
                "unit": r.get("unit"),
            }
            for r in results
        ]

        return {
            "ticker": ticker.upper(),
            "metric_name": metric_name,
            "year_count": len(time_series),
            "time_series": time_series,
            "raw": results,
        }

    @mcp.tool(name="compare_metric_across_companies")
    async def compare_metric_across_companies(
        tickers: list[str],
        metric_name: str,
        fiscal_year: int,
    ) -> dict:
        """
        Compare a single financial metric across multiple Magnificent 7
        companies for a specific fiscal year.

        Parameters
        ----------
        tickers : list[str]
            Two or more ticker symbols to compare,
            e.g. ``["AAPL", "MSFT", "GOOGL"]``.
        metric_name : str
            Financial metric name, e.g. ``"Revenue"``, ``"Net Margin"``.
        fiscal_year : int
            Four-digit fiscal year to compare (e.g. 2023).

        Returns
        -------
        dict with keys:
            metric_name, fiscal_year, comparison (list sorted by value desc)
        """
        # user = get_current_user()
        upper_tickers = [t.upper() for t in tickers]
        # user.assert_tickers(upper_tickers)

        allowed_tickers = upper_tickers
        # allowed_tickers = user.filter_tickers(upper_tickers)
        # if not allowed_tickers:
        #     raise PermissionError("You do not have access to any of the requested tickers.")

        svc = get_neo4j_service()
        results = svc.compare_metric_across_companies(
            tickers=allowed_tickers,
            metric_name=metric_name,
            fiscal_year=fiscal_year,
        )

        return {
            "metric_name": metric_name,
            "fiscal_year": fiscal_year,
            "companies_requested": upper_tickers,
            "companies_returned": allowed_tickers,
            "record_count": len(results),
            "comparison": results,
        }