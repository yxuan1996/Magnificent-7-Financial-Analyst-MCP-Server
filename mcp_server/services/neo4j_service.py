"""
neo4j_service.py
----------------
Wraps the Neo4j driver and exposes typed query methods that map to the
graph schema documented in the project README:

Financial facts schema
~~~~~~~~~~~~~~~~~~~~~~
  (:Company)
  (:Document)-[:BELONGS_TO]->(:Company)
  (:Document)-[:BELONGS_TO]->(:FiscalYear)
  (:Document)-[:REPORTS]->(:Fact)-[:FOR_METRIC]->(:Metric)

Key persons / developments schema
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  (:Document)-[:MENTIONS]->(:key_person)
  (:Document)-[:MENTIONS]->(:key_development)
"""

import logging
from contextlib import contextmanager
from typing import Any, Optional

from neo4j import GraphDatabase, Driver, Session

from mcp_server.config import settings

logger = logging.getLogger(__name__)


class Neo4jService:
    """Manages the Neo4j driver and provides domain-specific query methods."""

    def __init__(self) -> None:
        self._driver: Driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_username, settings.neo4j_password),
        )
        self._database: str = settings.neo4j_database

    def close(self) -> None:
        self._driver.close()

    @contextmanager
    def _session(self):
        session: Session = self._driver.session(database=self._database)
        try:
            yield session
        finally:
            session.close()

    def _run(self, cypher: str, **params) -> list[dict]:
        """Execute *cypher* and return results as plain dicts."""
        with self._session() as session:
            result = session.run(cypher, **params)
            records = [dict(record) for record in result]
        return records

    # ------------------------------------------------------------------
    # Financial facts
    # ------------------------------------------------------------------

    def get_financial_metric(
        self,
        ticker: str,
        metric_name: str,
        fiscal_year: Optional[int] = None,
    ) -> list[dict]:
        """
        Retrieve fact value(s) for a specific metric and company.

        Optional filter on fiscal year.
        """
        if fiscal_year is not None:
            cypher = """
            MATCH (c:Company {ticker: $ticker})
            MATCH (doc:Document)-[:BELONGS_TO]->(c)
            MATCH (doc)-[:BELONGS_TO]->(fy:FiscalYear {year: $fiscal_year})
            MATCH (doc)-[:REPORTS]->(fact:Fact)-[:FOR_METRIC]->(m:Metric)
            WHERE toLower(m.name) = toLower($metric_name)
            RETURN
                c.ticker          AS ticker,
                c.name            AS company_name,
                fy.year           AS fiscal_year,
                m.name            AS metric,
                m.unit            AS unit,
                fact.value        AS value,
                fact.period       AS period,
                doc.id            AS document_id
            ORDER BY fy.year DESC
            """
            params: dict = {"ticker": ticker.upper(), "metric_name": metric_name, "fiscal_year": fiscal_year}
        else:
            cypher = """
            MATCH (c:Company {ticker: $ticker})
            MATCH (doc:Document)-[:BELONGS_TO]->(c)
            MATCH (doc)-[:BELONGS_TO]->(fy:FiscalYear)
            MATCH (doc)-[:REPORTS]->(fact:Fact)-[:FOR_METRIC]->(m:Metric)
            WHERE toLower(m.name) = toLower($metric_name)
            RETURN
                c.ticker          AS ticker,
                c.name            AS company_name,
                fy.year           AS fiscal_year,
                m.name            AS metric,
                m.unit            AS unit,
                fact.value        AS value,
                fact.period       AS period,
                doc.id            AS document_id
            ORDER BY fy.year DESC
            """
            params = {"ticker": ticker.upper(), "metric_name": metric_name}

        return self._run(cypher, **params)

    def compare_metric_across_years(
        self,
        ticker: str,
        metric_name: str,
    ) -> list[dict]:
        """
        Return all available year-over-year values for a metric at one company.
        Results are sorted chronologically so callers can build time-series.
        """
        cypher = """
        MATCH (c:Company {ticker: $ticker})
        MATCH (doc:Document)-[:BELONGS_TO]->(c)
        MATCH (doc)-[:BELONGS_TO]->(fy:FiscalYear)
        MATCH (doc)-[:REPORTS]->(fact:Fact)-[:FOR_METRIC]->(m:Metric)
        WHERE toLower(m.name) = toLower($metric_name)
        RETURN
            c.ticker    AS ticker,
            c.name      AS company_name,
            fy.year     AS fiscal_year,
            m.name      AS metric,
            m.unit      AS unit,
            fact.value  AS value
        ORDER BY fy.year ASC
        """
        return self._run(cypher, ticker=ticker.upper(), metric_name=metric_name)

    def compare_metric_across_companies(
        self,
        tickers: list[str],
        metric_name: str,
        fiscal_year: int,
    ) -> list[dict]:
        """
        Compare a single metric across multiple companies for a given fiscal year.
        """
        upper_tickers = [t.upper() for t in tickers]
        cypher = """
        MATCH (c:Company)
        WHERE c.ticker IN $tickers
        MATCH (doc:Document)-[:BELONGS_TO]->(c)
        MATCH (doc)-[:BELONGS_TO]->(fy:FiscalYear {year: $fiscal_year})
        MATCH (doc)-[:REPORTS]->(fact:Fact)-[:FOR_METRIC]->(m:Metric)
        WHERE toLower(m.name) = toLower($metric_name)
        RETURN
            c.ticker    AS ticker,
            c.name      AS company_name,
            fy.year     AS fiscal_year,
            m.name      AS metric,
            m.unit      AS unit,
            fact.value  AS value
        ORDER BY fact.value DESC
        """
        return self._run(
            cypher,
            tickers=upper_tickers,
            metric_name=metric_name,
            fiscal_year=fiscal_year,
        )

    # ------------------------------------------------------------------
    # Key persons
    # ------------------------------------------------------------------

    def get_key_persons(
        self,
        ticker: str,
        role: Optional[str] = None,
    ) -> list[dict]:
        """
        Return key persons mentioned in annual report documents for *ticker*.
        Optionally filter by *role* (CEO, CFO, COO, Chairperson, BoardMember).
        """
        if role:
            cypher = """
            MATCH (c:Company {ticker: $ticker})
            MATCH (doc:Document)-[:BELONGS_TO]->(c)
            MATCH (doc)-[:MENTIONS]->(p:key_person)
            WHERE toLower(p.role) = toLower($role)
            RETURN DISTINCT
                p.name          AS name,
                p.role          AS role,
                p.description   AS description,
                c.ticker        AS ticker,
                c.name          AS company_name
            ORDER BY p.role, p.name
            """
            params: dict = {"ticker": ticker.upper(), "role": role}
        else:
            cypher = """
            MATCH (c:Company {ticker: $ticker})
            MATCH (doc:Document)-[:BELONGS_TO]->(c)
            MATCH (doc)-[:MENTIONS]->(p:key_person)
            RETURN DISTINCT
                p.name          AS name,
                p.role          AS role,
                p.description   AS description,
                c.ticker        AS ticker,
                c.name          AS company_name
            ORDER BY p.role, p.name
            """
            params = {"ticker": ticker.upper()}

        return self._run(cypher, **params)

    # ------------------------------------------------------------------
    # Key developments
    # ------------------------------------------------------------------

    def get_key_developments(
        self,
        ticker: str,
        category: Optional[str] = None,
        fiscal_year: Optional[int] = None,
    ) -> list[dict]:
        """
        Return key developments mentioned in annual report documents for *ticker*.
        Optionally filter by *category* and/or *fiscal_year*.
        """
        conditions = ["c.ticker = $ticker"]
        params: dict = {"ticker": ticker.upper()}

        if category:
            conditions.append("toLower(d.category) = toLower($category)")
            params["category"] = category
        if fiscal_year is not None:
            conditions.append("fy.year = $fiscal_year")
            params["fiscal_year"] = fiscal_year

        where_clause = " AND ".join(conditions)

        cypher = f"""
        MATCH (c:Company {{ticker: $ticker}})
        MATCH (doc:Document)-[:BELONGS_TO]->(c)
        MATCH (doc)-[:BELONGS_TO]->(fy:FiscalYear)
        MATCH (doc)-[:MENTIONS]->(d:key_development)
        WHERE {where_clause}
        RETURN DISTINCT
            d.title         AS title,
            d.category      AS category,
            d.description   AS description,
            d.date          AS date,
            fy.year         AS fiscal_year,
            c.ticker        AS ticker,
            c.name          AS company_name
        ORDER BY fy.year DESC, d.date DESC
        """
        return self._run(cypher, **params)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_neo4j_service: Optional[Neo4jService] = None


def get_neo4j_service() -> Neo4jService:
    global _neo4j_service
    if _neo4j_service is None:
        _neo4j_service = Neo4jService()
    return _neo4j_service