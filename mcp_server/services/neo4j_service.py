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

Troubleshooting empty results
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
If tool calls return empty lists, the most common cause is a mismatch
between the node labels / property names used in the Cypher queries and
what is actually stored in the database.

Use the ``inspect_graph`` MCP tool to discover the real schema:
    - Node labels present in the database
    - Relationship types present in the database
    - Property keys on each node label
    - Sample Company and Metric nodes

Compare the output against the expected schema above.
"""

import logging
from contextlib import contextmanager
from typing import Any, Optional

from neo4j import GraphDatabase, Driver, Session

from config import settings

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
        """
        Execute *cypher* and return results as plain Python dicts.

        Uses ``record.data()`` — the officially documented Neo4j Python
        driver method for converting a Record to a dict — instead of
        ``dict(record)``, which behaves inconsistently across driver
        versions because ``Record`` inherits from both ``tuple`` and
        ``Mapping``.

        Logs the query, parameters, and row count at DEBUG level so that
        empty results are immediately visible in the server log.
        """
        logger.debug(
            "Neo4j query | params=%s | cypher=%.200s",
            params,
            cypher.strip(),
        )
        with self._session() as session:
            result = session.run(cypher, **params)
            # record.data() is the correct, driver-documented way to
            # produce a plain dict from a neo4j Record object.
            records = [record.data() for record in result]

        logger.debug("Neo4j query | returned %d row(s)", len(records))

        if not records:
            logger.warning(
                "Neo4j returned 0 rows for params=%s — "
                "check node labels, relationship types, and property names "
                "against the actual database schema (use the inspect_graph tool).",
                params,
            )

        return records

    # ------------------------------------------------------------------
    # Schema introspection  (used by the inspect_graph diagnostic tool)
    # ------------------------------------------------------------------

    def get_schema(self) -> dict:
        """
        Return a summary of the database schema:
          - node_labels      : list of all label names
          - relationship_types: list of all relationship type names
          - node_properties  : {label: [property_key, ...]}
          - sample_companies : first 5 Company nodes (all properties)
          - sample_metrics   : first 10 Metric nodes (all properties)

        This method is intentionally broad — it helps diagnose mismatches
        between the expected schema in the Cypher queries and the real data.
        """
        schema: dict = {}

        # Node labels
        labels_result = self._run("CALL db.labels() YIELD label RETURN label")
        schema["node_labels"] = [r["label"] for r in labels_result]

        # Relationship types
        rels_result = self._run(
            "CALL db.relationshipTypes() YIELD relationshipType "
            "RETURN relationshipType"
        )
        schema["relationship_types"] = [
            r["relationshipType"] for r in rels_result
        ]

        # Property keys per node label
        props: dict = {}
        for label in schema["node_labels"]:
            # APOC not assumed — use a cheap LIMIT query instead
            try:
                rows = self._run(
                    f"MATCH (n:`{label}`) RETURN keys(n) AS k LIMIT 1"
                )
                props[label] = rows[0]["k"] if rows else []
            except Exception as exc:
                props[label] = [f"error: {exc}"]
        schema["node_properties"] = props

        # Sample Company nodes
        try:
            schema["sample_companies"] = self._run(
                "MATCH (c:Company) RETURN properties(c) AS props LIMIT 5"
            )
        except Exception as exc:
            schema["sample_companies"] = [{"error": str(exc)}]

        # Sample Metric nodes
        try:
            schema["sample_metrics"] = self._run(
                "MATCH (m:Metric) RETURN properties(m) AS props LIMIT 10"
            )
        except Exception as exc:
            schema["sample_metrics"] = [{"error": str(exc)}]

        # Sample FiscalYear nodes
        try:
            schema["sample_fiscal_years"] = self._run(
                "MATCH (fy:FiscalYear) RETURN properties(fy) AS props LIMIT 5"
            )
        except Exception as exc:
            schema["sample_fiscal_years"] = [{"error": str(exc)}]

        return schema

    def run_raw(self, cypher: str) -> list[dict]:
        """
        Execute an arbitrary read-only Cypher query and return results.
        Used by the ``inspect_graph`` diagnostic tool.
        """
        return self._run(cypher)

    # ------------------------------------------------------------------
    # Financial facts
    # ------------------------------------------------------------------

    def get_financial_metric(
        self,
        ticker: str,
        metric_name: str,
        fiscal_year: Optional[int] = None,
    ) -> list[dict]:
        """Retrieve fact value(s) for a specific metric and company."""
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
            params: dict = {
                "ticker": ticker.upper(),
                "metric_name": metric_name,
                "fiscal_year": fiscal_year,
            }
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
        """Return all available year-over-year values for a metric at one company."""
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
        """Compare a single metric across multiple companies for a given fiscal year."""
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
        """Return key persons mentioned in annual report documents for *ticker*."""
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
        """Return key developments mentioned in annual report documents for *ticker*."""
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