# Auth & Authorization Setup Guide

This guide configures **Supabase** as the single system for both
authentication (JWT issuance and verification) and authorization
(role-based access control via the RBAC tables).

---

## Auth flow overview

```
User logs in via Supabase Auth
        ↓
Supabase issues a signed JWT  (HS256, audience="authenticated")
        ↓
Client includes  Authorization: Bearer <jwt>  in every MCP request
        ↓
AuthMiddleware — Step 1: verify JWT signature + expiry
        ↓
AuthMiddleware — Step 2: query role_permissions
    Does the user's role grant access to this tool?
        ↓
AuthMiddleware — Step 3: derive allowed tickers from role names
    all_access → all 7 tickers
    Apple_only → AAPL only   … etc.
        ↓
Tool executes with a scoped UserContext
```

---

## Part 1 — Supabase project setup

### 1.1 Create a project

1. Sign up at [supabase.com](https://supabase.com) and create a new project.
2. From **Settings → API** copy:
   - `Project URL`    → `SUPABASE_URL`
   - `anon/public`    → `SUPABASE_ANON_KEY`
   - `service_role`   → `SUPABASE_SERVICE_ROLE_KEY`
3. From **Settings → API → JWT Settings** copy:
   - `JWT Secret`     → `SUPABASE_JWT_SECRET`

Add all four values to your `.env` file.

---

## Part 2 — RBAC schema

The three tables below must exist in your Supabase project.
Run this SQL in **Supabase Dashboard → SQL Editor**:

```sql
-- Roles  (e.g. all_access, Apple_only, Microsoft_only …)
create table if not exists roles (
  id   uuid primary key default gen_random_uuid(),
  name text unique not null
);

-- Assigns roles to Supabase Auth users
create table if not exists user_roles (
  user_id uuid references auth.users(id) on delete cascade,
  role_id uuid references roles(id)      on delete cascade,
  primary key (user_id, role_id)
);

-- Grants a role permission to call a specific MCP tool
create table if not exists role_permissions (
  role_id   uuid references roles(id) on delete cascade,
  tool_name text not null,
  primary key (role_id, tool_name)
);
```

> **Row Level Security** — these tables are queried by the MCP server
> using the `service_role` key (which bypasses RLS). Enable RLS on all
> three tables and add no policies — this ensures only the service role
> can read them, not logged-in end users.
>
> ```sql
> alter table roles            enable row level security;
> alter table user_roles       enable row level security;
> alter table role_permissions enable row level security;
> ```

---

## Part 3 — Seed roles and permissions

### 3.1 Create roles

```sql
insert into roles (name) values
  ('all_access'),
  ('Apple_only'),
  ('Microsoft_only'),
  ('Google_only'),
  ('Amazon_only'),
  ('Nvidia_only'),
  ('Meta_only'),
  ('Tesla_only')
on conflict (name) do nothing;
```

### 3.2 Grant tool permissions to each role

The MCP server exposes 7 tools. Grant them to roles as appropriate.

```sql
-- Helper: grant every tool to a given role by name
create or replace function grant_all_tools(role_name text)
returns void language plpgsql as $$
declare
  rid uuid;
begin
  select id into rid from roles where name = role_name;
  insert into role_permissions (role_id, tool_name)
  values
    (rid, 'search_report_text'),
    (rid, 'search_report_tables'),
    (rid, 'get_financial_metric'),
    (rid, 'compare_metric_across_years'),
    (rid, 'compare_metric_across_companies'),
    (rid, 'get_key_persons'),
    (rid, 'get_key_developments')
  on conflict do nothing;
end;
$$;

-- Grant all tools to every role
select grant_all_tools('all_access');
select grant_all_tools('Apple_only');
select grant_all_tools('Microsoft_only');
select grant_all_tools('Google_only');
select grant_all_tools('Amazon_only');
select grant_all_tools('Nvidia_only');
select grant_all_tools('Meta_only');
select grant_all_tools('Tesla_only');
```

> **Restricting tools per role** — if you want a role to access only a
> subset of tools, insert only those rows instead of calling the helper.

---

## Part 4 — Ticker access: role name conventions

Ticker access is derived **from the role name** — no extra table is needed.

| Role name        | Accessible tickers  |
|------------------|---------------------|
| `all_access`     | All 7 MAG7 tickers  |
| `Apple_only`     | AAPL                |
| `Microsoft_only` | MSFT                |
| `Google_only`    | GOOGL               |
| `Amazon_only`    | AMZN                |
| `Nvidia_only`    | NVDA                |
| `Meta_only`      | META                |
| `Tesla_only`     | TSLA                |

A user holding **multiple roles** gets the **union** of all tickers
covered by those roles. For example, a user with both `Apple_only` and
`Microsoft_only` can access AAPL and MSFT.

The mapping lives in `services/auth_service.py` (`TICKER_ROLE_MAP`). Add
new entries there when you introduce new roles.

---

## Part 5 — Create users and assign roles

### 5.1 Create a user

```sql
-- Via Supabase Dashboard: Authentication → Users → Invite user
-- Or via SQL (sets a confirmed email/password user):
select auth.admin_create_user(
  '{"email": "analyst@example.com",
    "password": "str0ng-p@ssword",
    "email_confirm": true}'::jsonb
);
```

### 5.2 Assign a role

```sql
-- Find the user's UUID
select id from auth.users where email = 'analyst@example.com';

-- Assign role  (replace the UUIDs)
insert into user_roles (user_id, role_id)
select
  '<user-uuid>',
  id
from roles
where name = 'all_access'   -- or 'Apple_only' etc.
on conflict do nothing;
```

### 5.3 Assign multiple roles to one user

```sql
insert into user_roles (user_id, role_id)
select '<user-uuid>', id from roles
where name in ('Apple_only', 'Microsoft_only')
on conflict do nothing;
```

---

## Part 6 — Obtain a JWT for testing

```bash
curl -s -X POST \
  'https://<project>.supabase.co/auth/v1/token?grant_type=password' \
  -H "apikey: <SUPABASE_ANON_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"email": "analyst@example.com", "password": "str0ng-p@ssword"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])"
```

---

## Part 7 — End-to-end test

```bash
# 1. Obtain JWT
TOKEN=$(curl -s -X POST \
  'https://<project>.supabase.co/auth/v1/token?grant_type=password' \
  -H "apikey: <SUPABASE_ANON_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"email":"analyst@example.com","password":"str0ng-p@ssword"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 2. Call a tool (all_access user — should succeed for any ticker)
curl -s -X POST http://localhost:8000/mcp \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0", "id": 1,
    "method": "tools/call",
    "params": {
      "name": "get_financial_metric",
      "arguments": {"ticker": "AAPL", "metric_name": "Revenue", "fiscal_year": 2023}
    }
  }'

# 3. Test ticker restriction (Apple_only user requesting MSFT — should fail)
curl -s -X POST http://localhost:8000/mcp \
  -H "Authorization: Bearer $APPLE_ONLY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0", "id": 2,
    "method": "tools/call",
    "params": {
      "name": "get_financial_metric",
      "arguments": {"ticker": "MSFT", "metric_name": "Revenue", "fiscal_year": 2023}
    }
  }'
# Expected: PermissionError — Access denied for ticker(s): MSFT
```

---

## Part 8 — Cache invalidation after role changes

The MCP server caches RBAC results for 5 minutes (`TTL = 300 s`).
After updating a user's roles in Supabase, call:

```python
from services.auth_service import get_auth_service
get_auth_service().invalidate_cache("<user-uuid>")
```

Or simply wait for the cache TTL to expire.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `Token has expired` | JWT older than 1 hour | Re-authenticate with Supabase |
| `Permission denied: your account is not authorised to call '<tool>'` | No `role_permissions` row for this user's role + tool | Run the `grant_all_tools()` helper for the role |
| `Permission denied: your account has no company data access` | User has no recognised role or role name doesn't match `TICKER_ROLE_MAP` | Assign a valid role; check spelling (case-sensitive) |
| `Invalid token` | Wrong `SUPABASE_JWT_SECRET` | Copy the exact secret from Supabase → Settings → API → JWT Settings |
| Stale permissions after role change | RBAC cache (5 min TTL) | Call `invalidate_cache(user_id)` or wait 5 minutes |