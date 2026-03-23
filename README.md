# Magnificent 7 Financial Analyst — MCP Server

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that
gives AI assistants structured access to annual report data for the
**Magnificent 7** tech companies:

| Ticker | Company       |
|--------|---------------|
| AAPL   | Apple         |
| MSFT   | Microsoft     |
| GOOGL  | Alphabet      |
| AMZN   | Amazon        |
| NVDA   | NVIDIA        |
| META   | Meta          |
| TSLA   | Tesla         |

[Data Ingestion for the Underlying Knowledge Base is described in this repo](https://github.com/yxuan1996/Magnificent-7-Financial-Analyst)

---

## Architecture

```
mcp_server/
├── main.py                  ← FastMCP app, uvicorn entrypoint, lifespan hooks
├── auth.py                  ← JWT middleware + UserContext helpers
├── config.py                ← Pydantic-settings (reads .env)
├── requirements.txt
├── .env                     ← secrets (gitignored)
├── .env.example             ← template to copy
│
├── tools/
│   ├── vector_tools.py      ← search_report_text, search_report_tables
│   ├── financial_tools.py   ← get_financial_metric, compare_*
│   ├── graph_tools.py       ← diagnostic and utility tools for Neo4j graph
│   ├── event_tools.py       ← get_key_developments
│   └── people_tools.py      ← get_key_persons
│
└── services/
    ├── pinecone_service.py  ← Pinecone index wrapper
    ├── neo4j_service.py     ← Neo4j Cypher query wrapper
    └── auth_service.py      ← (placeholder — auth removed)
```

### Knowledge Bases

| Store   | Contents |
|---------|----------|
| **Pinecone** | Paragraph text and Markdown tables chunked from Form 10-K annual reports. Text chunks carry a `text` metadata key; table chunks carry a `table_markdown` key. |
| **Neo4j** | Structured financial facts (`Company → Document → FiscalYear → Fact → Metric`), key persons, and key developments extracted from annual reports. |

---

## Setup

### 1. Clone and install dependencies

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure secrets

```bash
cp .env.example .env
# Edit .env with your real credentials
```

### 3. Run the server

```bash
# Development — run directly
fastmcp run mcp_server/main.py
```

The server starts on `http://localhost:8000` by default.

### 4. Health check

```bash
curl http://localhost:8000/health
# {"status": "ok", "server": "mag7-financial-analyst"}
```

---

## Connecting an MCP Client

### Claude Desktop / Claude.ai

Add the server to your MCP configuration file (usually
`~/.config/claude/claude_desktop_config.json` on macOS/Linux):

```json
{
  "mcpServers": {
    "mag7-analyst": {
      "url": "http://localhost:8000"
    }
  }
}
```

### Programmatic (Python — FastMCP client)

```python
from fastmcp import Client

async with Client("http://localhost:8000") as client:
    tools = await client.list_tools()
    result = await client.call_tool("get_financial_metric", {
        "ticker": "AAPL",
        "metric_name": "Revenue",
        "fiscal_year": 2023,
    })
```

### curl

```bash
curl http://localhost:8000/health
```



---

## Available Tools

### Vector Search Tools

#### `search_report_text`

Semantic search over **paragraph text** in annual reports.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | ✅ | Natural-language question |
| `tickers` | string[] | ❌ | Defaults to all allowed tickers |
| `top_k` | int | ❌ | Results to return (1–20, default 5) |
| `fiscal_year` | int | ❌ | Filter to a specific year |

**Example prompt:** *"What does Apple say about its services revenue strategy?"*

---

#### `search_report_tables`

Semantic search over **Markdown-formatted tables** in annual reports.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | ✅ | Natural-language question |
| `tickers` | string[] | ❌ | Defaults to all allowed tickers |
| `top_k` | int | ❌ | Results to return (1–20, default 5) |
| `fiscal_year` | int | ❌ | Filter to a specific year |

**Example prompt:** *"Find Microsoft's segment revenue breakdown table for 2023."*

---

### Financial Graph Tools

#### `get_financial_metric`

Retrieve a specific financial metric for one company.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `ticker` | string | ✅ | Company ticker, e.g. `"AAPL"` |
| `metric_name` | string | ✅ | e.g. `"Revenue"`, `"Net Income"`, `"EPS"` |
| `fiscal_year` | int | ❌ | Specific year; omit for all years |

---

#### `compare_metric_across_years`

Year-over-year time series for a metric at one company.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `ticker` | string | ✅ | Company ticker |
| `metric_name` | string | ✅ | Financial metric name |

---

#### `compare_metric_across_companies`

Side-by-side metric comparison across multiple Magnificent 7 companies.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `tickers` | string[] | ✅ | Two or more tickers |
| `metric_name` | string | ✅ | Financial metric name |
| `fiscal_year` | int | ✅ | Fiscal year for comparison |

---

### Key Persons Tool

#### `get_key_persons`

Retrieve executives and board members mentioned in annual reports.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `ticker` | string | ✅ | Company ticker |
| `role` | enum | ❌ | Filter by role (see below) |

**Role values:** `CEO` · `CFO` · `COO` · `Chairperson` · `BoardMember`

---

### Key Developments Tool

#### `get_key_developments`

Retrieve corporate events and developments from annual report filings.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `ticker` | string | ✅ | Company ticker |
| `category` | enum | ❌ | Filter by category (see below) |
| `fiscal_year` | int | ❌ | Filter to a specific year |

**Category values:**

| Value | Description |
|-------|-------------|
| `M&A` | Mergers, acquisitions, divestitures |
| `Restructuring` | Layoffs, reorgs, facility closures |
| `Litigation` | Lawsuits, fines, settlements |
| `ProductLaunch` | New products, services, platforms |
| `RegulatoryAction` | Government investigations, consent orders |
| `GuidanceChange` | Forward guidance revisions |

---

## Example Queries

```
# Compare revenue across all Mag-7 for FY2023
compare_metric_across_companies(
  tickers=["AAPL","MSFT","GOOGL","AMZN","NVDA","META","TSLA"],
  metric_name="Revenue",
  fiscal_year=2023
)

# Apple's net income over time
compare_metric_across_years(ticker="AAPL", metric_name="Net Income")

# NVIDIA product launches in 2023
get_key_developments(ticker="NVDA", category="ProductLaunch", fiscal_year=2023)

# Microsoft board members
get_key_persons(ticker="MSFT", role="BoardMember")

# Search for AI strategy discussion in GOOGL 2023 10-K
search_report_text(
  query="artificial intelligence strategy and investments",
  tickers=["GOOGL"],
  fiscal_year=2023
)
```

---

## Neo4j Graph Schema Reference

```
(:Company {ticker, name})
(:Document {id, type, filing_date})
(:FiscalYear {year})
(:Metric {name, unit})
(:Fact {value, period})
(:key_person {name, role, description})
(:key_development {title, category, description, date})

(Document)-[:BELONGS_TO]->(Company)
(Document)-[:BELONGS_TO]->(FiscalYear)
(Document)-[:REPORTS]->(Fact)
(Fact)-[:FOR_METRIC]->(Metric)
(Document)-[:MENTIONS]->(key_person)
(Document)-[:MENTIONS]->(key_development)
```

---

## License

MIT