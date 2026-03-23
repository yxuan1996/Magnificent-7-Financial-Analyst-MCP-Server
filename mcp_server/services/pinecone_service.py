"""
pinecone_service.py
-------------------
Wraps the Pinecone client and provides typed search helpers for:
  - Paragraph text chunks  (metadata key: "text")
  - Table chunks           (metadata key: "table_markdown")

Metadata keys (must match what was written at index time)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  company_ticker : str  — MAG7 ticker symbol, e.g. "AAPL"
  fiscal_year    : int  — four-digit year, e.g. 2023
  text           : str  — paragraph content  ← present on text chunks only
  table_markdown : str  — Markdown table     ← present on table chunks only

Chunk-type filtering
~~~~~~~~~~~~~~~~~~~~
There is NO ``chunk_type`` field in the index.  Chunk type is identified
by the *presence* of a metadata key:

  Text chunks   → filter: { "text":           { "$exists": true } }
  Table chunks  → filter: { "table_markdown":  { "$exists": true } }

This matches the pattern used in the working TypeScript client (vectorTools.ts).

Embeddings
~~~~~~~~~~
Vectors are generated with Azure OpenAI.  The following env vars are read
by the ``openai`` Python package's ``AzureOpenAI`` client:

  AZURE_OPENAI
  AZURE_OPENAI_ENDPOINT          (e.g. https://<instance>.openai.azure.com/)
  AZURE_OPENAI_API_EMBEDDINGS_DEPLOYMENT_NAME
  AZURE_OPENAI_API_VERSION           (e.g. 2024-02-01)
"""

import logging
from typing import Any, Optional

from pinecone import Pinecone
from openai import AzureOpenAI

from config import settings

logger = logging.getLogger(__name__)

# Pinecone metadata key that stores the company ticker.
# Must match the key written at index population time.
TICKER_METADATA_KEY = "company_ticker"


class PineconeService:
    """Manages the Pinecone index connection and semantic search queries."""

    def __init__(self) -> None:
        pc = Pinecone(api_key=settings.pinecone_api_key)
        self._index = pc.Index(settings.pinecone_index_name)

        # Azure OpenAI client for embedding generation.
        # Credentials come from environment variables (config.py exposes them
        # as typed attributes; the AzureOpenAI client reads them automatically).
        self._embed_client = AzureOpenAI(
          api_key=settings.azure_openai,
          azure_endpoint=settings.azure_openai_endpoint,
          api_version="2024-12-01-preview",
        )
        self._embed_deployment = settings.azure_openai_embeddings_deployment

        logger.info(
            "PineconeService ready | index=%s | azure_endpoint=%s | deployment=%s",
            settings.pinecone_index_name,
            settings.azure_openai_endpoint,
            self._embed_deployment,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _embed(self, text: str) -> list[float]:
        """Generate an embedding vector using the Azure OpenAI deployment."""
        response = self._embed_client.embeddings.create(
            input=text,
            model=self._embed_deployment,
        )
        vector = response.data[0].embedding
        logger.debug(
            "_embed | dimensions=%d | first_5=%s",
            len(vector),
            [round(v, 4) for v in vector[:5]],
        )
        return vector

    def _build_ticker_filter(self, tickers: list[str]) -> dict:
        """
        Build a Pinecone metadata filter restricting results to *tickers*.
        Uses ``$eq`` for a single ticker, ``$in`` for multiple.
        """
        upper = [t.upper() for t in tickers]
        if len(upper) == 1:
            return {TICKER_METADATA_KEY: {"$eq": upper[0]}}
        return {TICKER_METADATA_KEY: {"$in": upper}}

    def _parse_hit(self, match: Any) -> dict:
        """Convert a raw Pinecone match into a clean result dict."""
        meta: dict = match.metadata or {}
        return {
            "id":             match.id,
            "score":          round(match.score, 4),
            "ticker":         meta.get(TICKER_METADATA_KEY),
            "fiscal_year":    meta.get("fiscal_year"),
            "document_id":    meta.get("document_id"),
            "page":           meta.get("page"),
            "section":        meta.get("section"),
            "text":           meta.get("text"),
            "table_markdown": meta.get("table_markdown"),
        }

    def _warn_no_results(self, method: str, filter_used: dict) -> None:
        logger.warning(
            "%s returned 0 matches. filter=%s\n"
            "  Possible causes:\n"
            "  1. Metadata key mismatch — verify vectors have '%s' in metadata.\n"
            "  2. Chunk-type filter mismatch — text/table_markdown $exists may not match stored keys.\n"
            "  3. Azure embedding dimension doesn't match the index dimension.\n"
            "  4. Index is empty or wrong index name.",
            method,
            filter_used,
            TICKER_METADATA_KEY,
        )

    # ------------------------------------------------------------------
    # Public search methods
    # ------------------------------------------------------------------

    def search_report_text(
        self,
        query: str,
        tickers: list[str],
        top_k: int = 5,
        fiscal_year: Optional[int] = None,
    ) -> list[dict]:
        """
        Semantic search over **paragraph text** chunks.

        Chunk-type filter: ``{ "text": { "$exists": true } }``
        This identifies text chunks by the *presence* of the ``text``
        metadata key — NOT by a ``chunk_type`` field.
        """
        vector = self._embed(query)

        # Text chunks are identified by the existence of the "text" key,
        # matching the pattern in the working TypeScript client.
        metadata_filter: dict = {
            "$and": [
                {"text": {"$exists": True}},
                self._build_ticker_filter(tickers),
            ]
        }
        if fiscal_year is not None:
            metadata_filter["$and"].append({"fiscal_year": {"$eq": fiscal_year}})

        logger.debug("search_report_text | filter=%s", metadata_filter)

        result = self._index.query(
            vector=vector,
            top_k=top_k,
            include_metadata=True,
            filter=metadata_filter,
        )

        hits = [self._parse_hit(m) for m in (result.matches or [])]
        logger.info(
            "search_report_text | query=%r tickers=%s fy=%s → %d hits",
            query, tickers, fiscal_year, len(hits),
        )
        if not hits:
            self._warn_no_results("search_report_text", metadata_filter)
        return hits

    def search_report_tables(
        self,
        query: str,
        tickers: list[str],
        top_k: int = 5,
        fiscal_year: Optional[int] = None,
    ) -> list[dict]:
        """
        Semantic search over **table** chunks (returned as Markdown).

        Chunk-type filter: ``{ "table_markdown": { "$exists": true } }``
        This identifies table chunks by the *presence* of the
        ``table_markdown`` metadata key — NOT by a ``chunk_type`` field.
        """
        vector = self._embed(query)

        # Table chunks are identified by the existence of the "table_markdown" key.
        metadata_filter: dict = {
            "$and": [
                {"table_markdown": {"$exists": True}},
                self._build_ticker_filter(tickers),
            ]
        }
        if fiscal_year is not None:
            metadata_filter["$and"].append({"fiscal_year": {"$eq": fiscal_year}})

        logger.debug("search_report_tables | filter=%s", metadata_filter)

        result = self._index.query(
            vector=vector,
            top_k=top_k,
            include_metadata=True,
            filter=metadata_filter,
        )

        hits = [self._parse_hit(m) for m in (result.matches or [])]
        logger.info(
            "search_report_tables | query=%r tickers=%s fy=%s → %d hits",
            query, tickers, fiscal_year, len(hits),
        )
        if not hits:
            self._warn_no_results("search_report_tables", metadata_filter)
        return hits


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_pinecone_service: Optional[PineconeService] = None


def get_pinecone_service() -> PineconeService:
    global _pinecone_service
    if _pinecone_service is None:
        _pinecone_service = PineconeService()
    return _pinecone_service