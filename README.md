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
│   ├── event_tools.py       ← get_key_developments
│   └── people_tools.py      ← get_key_persons
│
└── services/
    ├── pinecone_service.py  ← Pinecone index wrapper
    ├── neo4j_service.py     ← Neo4j Cypher query wrapper
    └── auth_service.py      ← Supabase JWT verification + RBAC
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

Required values:

| Variable | Description |
|----------|-------------|
| `SUPABASE_URL` | Your Supabase project URL |
| `SUPABASE_ANON_KEY` | Supabase anon/public key |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service-role key (for RBAC queries) |
| `SUPABASE_JWT_SECRET` | JWT secret — found in Supabase → Settings → API → JWT Settings |
| `PINECONE_API_KEY` | Pinecone API key |
| `PINECONE_INDEX_NAME` | Name of your Pinecone index |
| `PINECONE_ENVIRONMENT` | Pinecone environment string |
| `NEO4J_URI` | Neo4j Bolt URI (e.g. `bolt://localhost:7687`) |
| `NEO4J_USERNAME` | Neo4j username |
| `NEO4J_PASSWORD` | Neo4j password |

### 3. Run the server

```bash
# Development — run directly
python main.py
```

The server starts on `http://localhost:8000` by default.

For production, invoke uvicorn directly so you can control workers,
reload behaviour, and TLS:

```bash
# Production — via uvicorn CLI
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
```

> **Why `--workers 1`?** FastMCP uses in-process `ContextVar` state and
> singleton service objects (Supabase client, Pinecone index, Neo4j driver)
> that cannot safely be forked across OS processes. For horizontal scaling,
> run multiple single-worker instances behind a load balancer (nginx,
> Caddy, etc.) instead of using multiple uvicorn workers in one process.

To enable auto-reload during development:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

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
      "url": "http://localhost:8000",
      "headers": {
        "Authorization": "Bearer <your-supabase-jwt>"
      }
    }
  }
}
```

### Programmatic (Python)

```python
import httpx

BASE = "http://localhost:8000"
JWT  = "eyJ..."   # obtain from Supabase Auth

headers = {"Authorization": f"Bearer {JWT}"}

# List available tools
resp = httpx.post(f"{BASE}/tools/list", headers=headers)
print(resp.json())
```

### curl (SSE transport)

```bash
curl -N -H "Authorization: Bearer $JWT" \
     -H "Content-Type: application/json" \
     -d '{"method":"tools/list","params":{}}' \
     http://localhost:8000/sse
```

---

## Authentication & Authorization

Every request must carry a **Supabase JWT** in the `Authorization` header:

```
Authorization: Bearer <token>
```

The `AuthMiddleware` in `auth.py`:
1. Verifies the token signature against `SUPABASE_JWT_SECRET`
2. Extracts the `sub` (user ID) from the JWT payload
3. Queries `role_permissions` to confirm the user's role grants access to the requested tool
4. Derives which tickers the user may access from their role names
5. Stores the resolved `UserContext` in a `ContextVar` for the duration of the tool call

Every tool calls `get_current_user()` to retrieve the scoped `UserContext`
and enforces ticker restrictions before touching any database.

### RBAC Tables (Supabase)

```sql
-- Assign tools to roles
INSERT INTO roles (name) VALUES
  ('all_access'), ('Apple_only'), ('Microsoft_only'), ...;

-- Grant tool access to a role
INSERT INTO role_permissions (role_id, tool_name)
SELECT id, 'search_report_text'  FROM roles WHERE name = 'all_access'
UNION ALL
SELECT id, 'get_financial_metric' FROM roles WHERE name = 'all_access'
-- ... etc.

-- Assign a user a role
INSERT INTO user_roles (user_id, role_id)
SELECT '<uuid>', id FROM roles WHERE name = 'Apple_only';
```

### Ticker Access Rules

| Role | Accessible tickers |
|------|--------------------|
| `all_access` | All 7 tickers |
| `Apple_only` | AAPL |
| `Microsoft_only` | MSFT |
| `Google_only` | GOOGL |
| `Amazon_only` | AMZN |
| `Nvidia_only` | NVDA |
| `Meta_only` | META |
| `Tesla_only` | TSLA |

Users can hold multiple roles (e.g. `Apple_only` + `Microsoft_only` allows
access to both AAPL and MSFT).

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