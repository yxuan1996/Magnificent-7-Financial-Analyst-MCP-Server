"""
graph_tools.py
--------------
Diagnostic MCP tools for inspecting the Neo4j graph database.

These tools are intended for development and troubleshooting — specifically
for diagnosing empty results from the financial/person/development tools.

Tools
~~~~~
- inspect_graph  → node labels, relationship types, property keys, sample data
- run_cypher     → execute any read-only Cypher query and return raw results
"""

import logging

from fastmcp import FastMCP

from services.neo4j_service import get_neo4j_service

logger = logging.getLogger(__name__)

TOOL_INSPECT_GRAPH = "inspect_graph"
TOOL_RUN_CYPHER    = "run_cypher"


def register_graph_tools(mcp: FastMCP) -> None:
    """Register diagnostic graph tools onto *mcp*."""

    @mcp.tool(name=TOOL_INSPECT_GRAPH)
    async def inspect_graph() -> dict:
        """
        Inspect the Neo4j database schema and return sample data.

        Use this tool when financial, person, or development tools return
        empty results. It reveals:

        - **node_labels** — every node label present in the database
        - **relationship_types** — every relationship type present
        - **node_properties** — property keys on each node label
        - **sample_companies** — up to 5 Company nodes (all properties)
        - **sample_metrics** — up to 10 Metric nodes (all properties)
        - **sample_fiscal_years** — up to 5 FiscalYear nodes (all properties)

        Compare the output against the expected schema:

        Expected node labels:
            Company, Document, FiscalYear, Fact, Metric, key_person, key_development

        Expected relationship types:
            BELONGS_TO, REPORTS, FOR_METRIC, MENTIONS

        Expected Company properties:
            ticker (e.g. "AAPL"), name (e.g. "Apple Inc.")

        Expected FiscalYear properties:
            year (integer, e.g. 2023)

        Expected Metric properties:
            name (e.g. "Revenue"), unit (e.g. "USD millions")

        If the actual schema differs (different label names, property names,
        or relationship directions), the Cypher queries in neo4j_service.py
        must be updated to match the real data.
        """
        svc = get_neo4j_service()
        schema = svc.get_schema()
        logger.info("inspect_graph | schema returned: %s", list(schema.keys()))
        return schema

    @mcp.tool(name=TOOL_RUN_CYPHER)
    async def run_cypher(cypher: str) -> dict:
        """
        Execute a read-only Cypher query against the Neo4j database and
        return the raw results.

        Use this for targeted diagnosis when ``inspect_graph`` reveals a
        schema mismatch and you need to explore specific nodes or paths.

        Parameters
        ----------
        cypher : str
            A read-only Cypher query. Examples:

            -- Count all nodes by label
            MATCH (n) RETURN labels(n) AS label, count(n) AS count ORDER BY count DESC

            -- See all properties on Company nodes
            MATCH (c:Company) RETURN properties(c) LIMIT 10

            -- Check whether AAPL exists
            MATCH (c:Company) WHERE c.ticker = "AAPL" RETURN c LIMIT 1

            -- Trace the path from Company to Metric
            MATCH p = (c:Company)-[*1..4]-(m:Metric)
            WHERE c.ticker = "AAPL"
            RETURN [n in nodes(p) | labels(n)] AS node_labels,
                   [r in relationships(p) | type(r)] AS rel_types
            LIMIT 5

            -- Find all metric names available for AAPL
            MATCH (c:Company {ticker: "AAPL"})
            MATCH (doc:Document)-[:BELONGS_TO]->(c)
            MATCH (doc)-[:REPORTS]->(fact:Fact)-[:FOR_METRIC]->(m:Metric)
            RETURN DISTINCT m.name AS metric ORDER BY metric

        Returns
        -------
        dict with keys:
            row_count : int
            rows      : list of result records as dicts
        """
        svc = get_neo4j_service()
        try:
            rows = svc.run_raw(cypher)
            return {"row_count": len(rows), "rows": rows}
        except Exception as exc:
            logger.error("run_cypher error: %s", exc)
            return {"row_count": 0, "rows": [], "error": str(exc)}