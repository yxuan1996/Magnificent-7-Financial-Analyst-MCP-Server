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

Metric name resolution
~~~~~~~~~~~~~~~~~~~~~~
All three tools use the ``metricNameIndex`` fulltext index for metric
name matching.  The query is built as:

    *<term>* OR <term>~2          (single word)
    *<term>* OR (word1~2 AND word2~2)   (multiple words)

This means:
  - "revenue"        matches "Revenue", "revenues", "Net Revenue", etc.
  - "net income"     matches "Net Income", "Net Income (Loss)", etc.
  - "eps"            matches "EPS", "Diluted EPS", "Basic EPS", etc.

Results include a ``search_score`` column (Lucene relevance score) so
callers can see how closely the matched metric name aligns with the input.
The highest-scoring metric is returned first.

Use the ``search_metrics`` tool to discover exact metric names in the
index before querying.

Tools
~~~~~
- get_financial_metric            → single company, one metric, optional year
- compare_metric_across_years     → single company, one metric, all years
- compare_metric_across_companies → multiple companies, one metric, one year
"""

import logging
from typing import Optional

from fastmcp import FastMCP

from services.neo4j_service import get_neo4j_service

logger = logging.getLogger(__name__)

TOOL_GET_FINANCIAL_METRIC  = "get_financial_metric"
TOOL_COMPARE_YEARS         = "compare_metric_across_years"
TOOL_COMPARE_COMPANIES     = "compare_metric_across_companies"


def register_financial_tools(mcp: FastMCP) -> None:
    """Register all financial graph tools onto *mcp*."""

    @mcp.tool(name=TOOL_GET_FINANCIAL_METRIC)
    async def get_financial_metric(
        ticker: str,
        metric_name: str,
        fiscal_year: Optional[int] = None,
    ) -> dict:
        """
        Retrieve a financial metric value for a single company.

        Metric name matching uses the ``metricNameIndex`` fulltext index with
        wildcard and fuzzy (edit-distance 2) matching, so approximate names
        work — e.g. "revenue", "revenues", "net rev" all resolve correctly.
        Results are ordered by match relevance first, then fiscal year descending.

        Parameters
        ----------
        ticker : str
            Company ticker symbol, e.g. ``"AAPL"``.
        metric_name : str
            Approximate name of the financial metric, e.g. ``"Revenue"``,
            ``"Net Income"``, ``"EPS"``, ``"Operating Cash Flow"``.
            Spelling does not need to be exact.
        fiscal_year : int, optional
            Four-digit fiscal year (e.g. 2023). If omitted, all available
            years are returned, most recent first.

        Returns
        -------
        dict with keys:
            ticker           : str
            metric_name      : str  (the input name)
            fiscal_year_filter : int | None
            record_count     : int
            facts            : list of {ticker, company_name, fiscal_year,
                                         metric, unit, value, period,
                                         document_id, search_score}
        """
        svc = get_neo4j_service()
        results = svc.get_financial_metric(
            ticker=ticker.upper(),
            metric_name=metric_name,
            fiscal_year=fiscal_year,
        )

        # Surface the top matched metric name for transparency
        matched_metric = results[0]["metric"] if results else None

        return {
            "ticker": ticker.upper(),
            "metric_name": metric_name,
            "matched_metric": matched_metric,
            "fiscal_year_filter": fiscal_year,
            "record_count": len(results),
            "facts": results,
        }

    @mcp.tool(name=TOOL_COMPARE_YEARS)
    async def compare_metric_across_years(
        ticker: str,
        metric_name: str,
    ) -> dict:
        """
        Return all available yearly values for a metric at one company,
        ordered chronologically — useful for trend analysis and charts.

        Metric name matching uses ``metricNameIndex`` fulltext fuzzy search.
        The highest-scoring match is used; all its yearly values are returned
        sorted oldest-to-newest so you can read the trend left-to-right.

        Parameters
        ----------
        ticker : str
            Company ticker symbol, e.g. ``"MSFT"``.
        metric_name : str
            Approximate financial metric name, e.g. ``"Revenue"``,
            ``"Free Cash Flow"``, ``"Gross Margin"``.

        Returns
        -------
        dict with keys:
            ticker         : str
            metric_name    : str  (the input name)
            matched_metric : str | None  (the actual stored metric name)
            year_count     : int
            time_series    : list of {fiscal_year, value, unit, search_score}
        """
        svc = get_neo4j_service()
        results = svc.compare_metric_across_years(
            ticker=ticker.upper(),
            metric_name=metric_name,
        )

        matched_metric = results[0]["metric"] if results else None

        time_series = [
            {
                "fiscal_year": r["fiscal_year"],
                "value": r["value"],
                "unit": r.get("unit"),
                "search_score": r.get("search_score"),
            }
            for r in results
        ]

        return {
            "ticker": ticker.upper(),
            "metric_name": metric_name,
            "matched_metric": matched_metric,
            "year_count": len(time_series),
            "time_series": time_series,
        }

    @mcp.tool(name=TOOL_COMPARE_COMPANIES)
    async def compare_metric_across_companies(
        tickers: list[str],
        metric_name: str,
        fiscal_year: int,
    ) -> dict:
        """
        Compare a single financial metric across multiple Magnificent 7
        companies for a specific fiscal year.

        Metric name matching uses ``metricNameIndex`` fulltext fuzzy search.
        Results are ordered by metric relevance score first, then value
        descending so the highest value appears first within each metric match.

        Parameters
        ----------
        tickers : list[str]
            Two or more ticker symbols, e.g. ``["AAPL", "MSFT", "GOOGL"]``.
        metric_name : str
            Approximate financial metric name, e.g. ``"Revenue"``.
        fiscal_year : int
            Four-digit fiscal year (e.g. 2023).

        Returns
        -------
        dict with keys:
            metric_name        : str
            matched_metric     : str | None
            fiscal_year        : int
            companies_requested: list[str]
            record_count       : int
            comparison         : list of {ticker, company_name, fiscal_year,
                                           metric, unit, value, search_score}
        """
        upper_tickers = [t.upper() for t in tickers]
        svc = get_neo4j_service()
        results = svc.compare_metric_across_companies(
            tickers=upper_tickers,
            metric_name=metric_name,
            fiscal_year=fiscal_year,
        )

        matched_metric = results[0]["metric"] if results else None

        return {
            "metric_name": metric_name,
            "matched_metric": matched_metric,
            "fiscal_year": fiscal_year,
            "companies_requested": upper_tickers,
            "record_count": len(results),
            "comparison": results,
        }