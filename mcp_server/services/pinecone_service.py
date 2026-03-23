"""
pinecone_service.py
-------------------
Wraps the Pinecone client and provides typed search helpers for:
  - Paragraph text chunks  (metadata key: "text")
  - Table chunks           (metadata key: "table_markdown")

Metadata keys
~~~~~~~~~~~~~
  company_ticker : str   — MAG7 ticker symbol (e.g. "AAPL")
  chunk_type     : str   — "paragraph" or "table"
  fiscal_year    : int   — four-digit year
  text           : str   — paragraph content (paragraph chunks only)
  table_markdown : str   — Markdown table (table chunks only)

Both search methods accept an optional *tickers* filter so that only
documents belonging to allowed companies are returned.
"""

import logging
from typing import Any, Optional

from pinecone import Pinecone
from openai import AzureOpenAI

from config import settings

logger = logging.getLogger(__name__)

# Metadata field that identifies which company a chunk belongs to.
# Must match the key used when the index was populated.
TICKER_METADATA_KEY = "company_ticker"

# Metadata field that distinguishes chunk types.
CHUNK_TYPE_KEY = "chunk_type"
TEXT_CHUNK_TYPE = "paragraph"
TABLE_CHUNK_TYPE = "table"


class PineconeService:
    """Manages the Pinecone index connection and semantic search queries."""

    def __init__(self) -> None:
        pc = Pinecone(api_key=settings.pinecone_api_key)
        self._index = pc.Index(settings.pinecone_index_name)
        # Re-use OpenAI embeddings (swap model/client as needed)
        self._embed_client = AzureOpenAI(
          api_key=settings.azure_openai,
          azure_endpoint=settings.azure_openai_endpoint,
          api_version="2024-12-01-preview",
    )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _embed(self, text: str) -> list[float]:
        """Generate an embedding vector for *text*."""
        response = self._embed_client.embeddings.create(
            input=text,
            model="text-embedding-3-small",
        )
        return response.data[0].embedding

    def _build_ticker_filter(self, tickers: list[str]) -> dict:
        """
        Build a Pinecone metadata filter restricting results to *tickers*.
        Uses ``$in`` operator when multiple tickers are supplied.
        """
        if len(tickers) == 1:
            return {TICKER_METADATA_KEY: {"$eq": tickers[0].upper()}}
        return {TICKER_METADATA_KEY: {"$in": [t.upper() for t in tickers]}}

    def _parse_hit(self, match: Any) -> dict:
        """Convert a raw Pinecone match into a clean result dict."""
        meta: dict = match.metadata or {}
        return {
            "id": match.id,
            "score": round(match.score, 4),
            "ticker": meta.get("company_ticker"),  # stored as company_ticker in Pinecone
            "fiscal_year": meta.get("fiscal_year"),
            "document_id": meta.get("document_id"),
            "page": meta.get("page"),
            "section": meta.get("section"),
            "text": meta.get("text"),
            "table_markdown": meta.get("table_markdown"),
        }

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
        Semantic search over paragraph text chunks.

        Filters:
        - chunk_type == "paragraph"  (only text chunks, not tables)
        - ticker     in *tickers*
        - fiscal_year == *fiscal_year*  (optional)
        """
        vector = self._embed(query)

        metadata_filter: dict = {
            "$and": [
                {CHUNK_TYPE_KEY: {"$eq": TEXT_CHUNK_TYPE}},
                self._build_ticker_filter(tickers),
            ]
        }
        if fiscal_year is not None:
            metadata_filter["$and"].append({"fiscal_year": {"$eq": fiscal_year}})

        result = self._index.query(
            vector=vector,
            top_k=top_k,
            include_metadata=True,
            filter=metadata_filter,
        )

        hits = [self._parse_hit(m) for m in result.matches]
        logger.info(
            "search_report_text | query=%r tickers=%s fy=%s → %d hits",
            query,
            tickers,
            fiscal_year,
            len(hits),
        )
        return hits

    def search_report_tables(
        self,
        query: str,
        tickers: list[str],
        top_k: int = 5,
        fiscal_year: Optional[int] = None,
    ) -> list[dict]:
        """
        Semantic search over table chunks (returned as Markdown).

        Filters:
        - chunk_type == "table"
        - ticker     in *tickers*
        - fiscal_year == *fiscal_year*  (optional)
        """
        vector = self._embed(query)

        metadata_filter: dict = {
            "$and": [
                {CHUNK_TYPE_KEY: {"$eq": TABLE_CHUNK_TYPE}},
                self._build_ticker_filter(tickers),
            ]
        }
        if fiscal_year is not None:
            metadata_filter["$and"].append({"fiscal_year": {"$eq": fiscal_year}})

        result = self._index.query(
            vector=vector,
            top_k=top_k,
            include_metadata=True,
            filter=metadata_filter,
        )

        hits = [self._parse_hit(m) for m in result.matches]
        logger.info(
            "search_report_tables | query=%r tickers=%s fy=%s → %d hits",
            query,
            tickers,
            fiscal_year,
            len(hits),
        )
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