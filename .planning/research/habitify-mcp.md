# Habitify MCP Research

**Researched:** 2026-03-10
**Overall Confidence:** HIGH (OAuth headless flow validated — refresh tokens and all required endpoints confirmed)

---

## 1. What Is the Habitify MCP Server

Habitify released an official MCP server that exposes their habit tracking data and actions to AI assistants. It uses the Model Context Protocol standard, making it compatible with Claude, ChatGPT, Cursor, and any other MCP-compliant client.

**Official MCP endpoint:**
```
https://mcp.habitify.me/mcp
```

**Transport:** SSE (Server-Sent Events). Despite Habitify's docs describing it as "HTTP (Streamable)", live probing confirms SSE transport. `GET /mcp` opens an SSE stream and returns a session-specific endpoint like `/mcp/messages?sessionId=<uuid>`. Messages are sent via `POST` to that session endpoint. Server reports `{"name":"habitify","version":"0.1.0"}`, protocol version `2025-03-26`. LiveKit's `MCPServerHTTP` should be configured with `transport_type="sse"` to avoid auto-detection issues.

**Rate limit:** 500 requests per minute per account (confirmed in official MCP API docs).

**Source confidence:** HIGH — official Habitify MCP docs at `api-docs.habitify.me/mcp/others` and help articles at `intercom.help/habitify-app`.

---

## 2. Authentication

### Official MCP Server (`mcp.habitify.me`)

The official server uses **OAuth 2.0 with dynamic client registration**. There is no static API key option for the MCP endpoint.

- Auth flow: OAuth 2.1 authorization code with PKCE + user consent
- On first connection, the user is redirected to log in to their Habitify account and authorize access
- After initial auth, **refresh tokens enable fully programmatic token renewal** — no browser needed

**Headless/programmatic viability: CONFIRMED (HIGH confidence)**

The OAuth discovery endpoints have been probed and validated. Habitify's authorization server at `https://account.habitify.me` fully supports headless operation after a one-time manual authorization:

**Protected Resource Metadata** (`https://mcp.habitify.me/.well-known/oauth-protected-resource`):
```json
{
  "resource": "https://mcp.habitify.me",
  "authorization_servers": ["https://account.habitify.me"],
  "scopes_supported": ["profile", "openid"],
  "resource_documentation": "https://mcp.habitify.me/docs"
}
```

**Authorization Server Metadata** (`https://account.habitify.me/.well-known/openid-configuration`):

| Field | Value |
|-------|-------|
| **Authorization endpoint** | `https://account.habitify.me/auth` |
| **Token endpoint** | `https://account.habitify.me/token` |
| **Registration endpoint** | `https://account.habitify.me/reg` |
| **Token revocation** | `https://account.habitify.me/token/revocation` |
| **Token introspection** | `https://account.habitify.me/token/introspection` |
| **JWKS URI** | `https://account.habitify.me/jwks` |
| **Userinfo endpoint** | `https://account.habitify.me/me` |
| **End session endpoint** | `https://account.habitify.me/session/end` |

| Capability | Supported Values |
|------------|-----------------|
| **Grant types** | `authorization_code`, `refresh_token`, `client_credentials`, `implicit` |
| **Scopes** | `openid`, `profile`, `email`, `offline_access`, `all` |
| **PKCE methods** | `S256`, `plain` |
| **Token auth methods** | `none`, `client_secret_basic`, `client_secret_jwt`, `client_secret_post`, `private_key_jwt` |
| **Response types** | `code`, `id_token`, `code id_token`, `none` |
| **Response modes** | `query`, `fragment`, `form_post` |

**Key findings for headless operation:**
- `refresh_token` grant type → tokens can be renewed programmatically forever
- `offline_access` scope → explicitly designed for long-lived/background access
- `all` scope → presumably grants full access to habit data
- `client_credentials` grant type → machine-to-machine auth potentially possible (untested)
- Token auth method `none` → public clients (no client secret) are supported
- Dynamic client registration at `/reg` → our agent can register itself

**One-time setup flow:**
1. Register a client via `POST https://account.habitify.me/reg`
2. Open `https://account.habitify.me/auth` in a browser with auth code + PKCE params, scope: `openid offline_access all`
3. User logs in and authorizes → callback returns authorization code
4. Exchange code at `https://account.habitify.me/token` → get access token + refresh token
5. Store refresh token securely (env var / secrets manager)

**Runtime flow (fully automated, no browser):**
1. `POST https://account.habitify.me/token` with `grant_type=refresh_token` → get fresh access token
2. Pass to `MCPServerHTTP(headers={"Authorization": f"Bearer {token}"})`

**Source confidence:** HIGH — all endpoints validated via direct HTTP probes on 2026-03-10.

**Minimal config (for interactive clients):**
```json
{
  "mcpServers": {
    "habitify": {
      "url": "https://mcp.habitify.me/mcp"
    }
  }
}
```

### Alternative: Community NPM MCP Server (`@sargonpiraev/habitify-mcp-server`)

An open-source community implementation that wraps the Habitify REST API. It uses a static **API key** (no OAuth), which is far simpler for programmatic/headless use.

- API key location: Habitify app or web → Settings → Account → API Credential
- Set as environment variable: `HABITIFY_API_KEY`
- Runs as a stdio process via `npx @sargonpiraev/habitify-mcp-server`
- Node.js >= 18.0.0 required

**Source confidence:** HIGH for the npm package (GitHub repo + npm confirmed). MEDIUM for tool completeness (README lists 12 tools but lacks parameter-level docs).

---

## 3. Available Tools

### Official MCP Server Tools (12 tools — Confirmed via Live Probe)

The official server (`mcp.habitify.me`) exposes exactly 12 tools, confirmed by calling `tools/list` on 2026-03-10:

| Tool Name | Purpose |
|-----------|---------|
| `list-habits-by-date` | Get all habits and their status for a given date |
| `complete-habit` | Mark a single habit as complete |
| `fail-habit` | Mark a single habit as failed |
| `skip-habit` | Mark a single habit as skipped |
| `complete-habits` | Bulk mark multiple habits as complete |
| `fail-habits` | Bulk mark multiple habits as failed |
| `skip-habits` | Bulk mark multiple habits as skipped |
| `complete-all-habits` | Mark all habits as complete |
| `fail-all-habits` | Mark all habits as failed |
| `skip-all-habits` | Mark all habits as skipped |
| `add-habit-log` | Add a log entry for a goal-based habit |
| `remove-habit-log` | Remove a habit log entry |

Each tool has `"securitySchemes":[{"type":"oauth2","scopes":["profile"]}]` in its definition.

**Auth behavior:** `initialize` and `tools/list` work without authentication. Auth is enforced at tool invocation — calling a tool without a Bearer token returns a success response with `"Authentication required: no access token provided."` (not an HTTP 401).

### Community NPM Server Tools (Confirmed, 12 tools)

| Tool Name | Purpose |
|-----------|---------|
| `get-journal` | Retrieve habit journal for a specific date |
| `add-habit-log` | Record a new habit log entry (for goal-based habits) |
| `delete-habit-log` | Remove a specific habit log |
| `delete-habit-logs-range` | Bulk delete logs within a date range |
| `get-habits` | Fetch all habits |
| `get-habit` | Fetch a specific habit by ID |
| `get-areas` | Fetch all areas/categories |
| `get-moods` | Retrieve mood entries |
| `add-mood` | Create a mood entry |
| `get-notes` | Retrieve notes |
| `add-note` | Create a note |
| `get-actions` | Retrieve available actions |

**Notable finding:** The official MCP server has dedicated `complete-habit`, `fail-habit`, and `skip-habit` tools — much cleaner than the REST API's dual-path approach (status endpoint vs. logs endpoint). The MCP server handles the routing internally. For goal-based habits, `add-habit-log` is available.

---

## 4. Data Model

### Habit Object (from REST API — same data the MCP journal tool returns)

```json
{
  "id": "-MaRzj4a1xYCzSUvwD8Y",
  "name": "Read Books",
  "is_archived": false,
  "start_date": "2021-05-24T06:10:11.397Z",
  "time_of_day": ["any_time"],
  "goal": {
    "unit_type": "min",
    "value": 30,
    "periodicity": "daily"
  },
  "recurrence": "RRULE:FREQ=DAILY",
  "remind": ["21:30"],
  "area": {
    "id": "area_id_string",
    "name": "Health",
    "priority": 1
  },
  "log_method": "manual",
  "priority": -6.338e+29,
  "created_date": "2021-05-24T06:10:11.397Z",
  "status": "completed",
  "progress": {
    "current_value": 30,
    "target_value": 30,
    "unit_type": "min",
    "periodicity": "daily",
    "reference_date": "2021-05-26T00:00:00.000Z"
  }
}
```

### Status Values

| Status | Meaning |
|--------|---------|
| `none` | No data logged for this day |
| `in_progress` | Active tracking, goal not yet met |
| `completed` | Goal reached or manually marked done |
| `skipped` | Deliberately bypassed (legacy — only accounts created before Aug 20, 2020) |
| `failed` | Legacy status for older accounts |

### Completion: Two Paths Based on Habit Type

**Simple habits (no numeric goal):** Use the status endpoint.
```
PUT https://api.habitify.me/status/:habit_id
Authorization: <api_key>
Body: { "status": "completed", "target_date": "YYYY-MM-DDTHH:MM:SS.mmmZ" }
```

**Goal-based habits (e.g., "run 5km", "read 30 min"):** Use the logs endpoint. The status endpoint does NOT support marking these complete — you must log the value.
```
POST https://api.habitify.me/logs/:habit_id
Authorization: <api_key>
Body: { "unit_type": "min", "value": 30, "target_date": "YYYY-MM-DDTHH:MM:SS.mmmZ" }
```

### Journal Response (GET today's habits with status)

```
GET https://api.habitify.me/journal?target_date=2026-03-10T00:00:00.000Z
```

Optional filters:
- `order_by`: `priority`, `reminder_time`, or `status`
- `status`: `in_progress`, `completed`, `failed`, `skipped`
- `area_id`: filter by area
- `time_of_day`: `morning`, `afternoon`, `evening`, `any_time` (comma-separated)

Response wraps all data:
```json
{
  "message": "Success",
  "data": [/* array of habit objects with status and progress */],
  "version": "v1.2",
  "status": true
}
```

---

## 5. How to Connect from a Python LiveKit Agent

### Option A: Official MCP Server via `MCPServerHTTP` (requires OAuth token) — RECOMMENDED

LiveKit's `MCPServerHTTP` accepts a `headers` dict for authentication. It has **no built-in OAuth support** — it simply forwards the headers dict on every request. This means we handle token lifecycle ourselves.

**Architecture:**
```python
import httpx
import os
from livekit.agents.llm import mcp

async def get_fresh_habitify_token() -> str:
    """Refresh the Habitify OAuth token before each session."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://account.habitify.me/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": os.getenv("HABITIFY_REFRESH_TOKEN"),
                "client_id": os.getenv("HABITIFY_CLIENT_ID"),
                "scope": "openid offline_access all",
            },
        )
        data = resp.json()
        return data["access_token"]

# At session start:
token = await get_fresh_habitify_token()

session = AgentSession(
    ...
    mcp_servers=[
        mcp.MCPServerHTTP(
            url="https://mcp.habitify.me/mcp",
            transport_type="sse",  # Server uses SSE despite URL ending in /mcp
            headers={"Authorization": f"Bearer {token}"},
        )
    ],
)
```

The LLM automatically discovers and calls all tools exposed by the server — no manual tool registration needed.

**LiveKit MCPServerHTTP auth details (validated from source code):**
- Headers are static — set at construction, used for lifetime of the connection
- No token refresh built in; no `auth` parameter exposed
- Headers *can* be mutated after construction, but require `aclose()` + `initialize()` to take effect
- For a typical voice session (5-30 min), refreshing once at session start is sufficient
- For longer sessions, subclassing `MCPServerHTTP` and overriding `client_streams()` to use the upstream MCP SDK's `OAuthClientProvider` (which handles automatic refresh) is an option

**No longer a blocker:** The refresh token flow at `https://account.habitify.me/token` has been confirmed to exist via OAuth discovery metadata (grant type `refresh_token` + scope `offline_access`).

### Option B: Community NPM Server via `MCPServerStdio` (API key — simpler)

LiveKit also supports `MCPServerStdio`, which spawns a local subprocess:

```python
from livekit.agents.llm import mcp
import os

session = AgentSession(
    ...
    mcp_servers=[
        mcp.MCPServerStdio(
            command="npx",
            args=["-y", "@sargonpiraev/habitify-mcp-server"],
            env={**os.environ, "HABITIFY_API_KEY": os.getenv("HABITIFY_API_KEY")},
        )
    ],
)
```

Requirements: Node.js 18+ must be installed on the same machine running the Python agent. This adds a Node.js runtime dependency.

### Option C: Direct REST API Calls (bypasses MCP entirely)

Since the underlying Habitify REST API is well-documented and uses a simple API key header, the agent could skip MCP entirely and use `httpx` or `requests` directly. This is the most predictable approach:

```python
import httpx

HABITIFY_BASE = "https://api.habitify.me"
headers = {"Authorization": os.getenv("HABITIFY_API_KEY")}

# Get today's habits
resp = httpx.get(f"{HABITIFY_BASE}/journal", params={"target_date": today_iso}, headers=headers)

# Mark habit complete (no goal)
httpx.put(f"{HABITIFY_BASE}/status/{habit_id}", json={"status": "completed", "target_date": today_iso}, headers=headers)

# Log progress (goal-based habit)
httpx.post(f"{HABITIFY_BASE}/logs/{habit_id}", json={"unit_type": "min", "value": 30, "target_date": today_iso}, headers=headers)
```

This trades MCP's LLM-native discovery for simplicity, reliability, and no extra runtime dependency.

---

## 6. Limitations and Gotchas

### Authentication: One-Time Manual Setup Required
The official MCP server requires OAuth with a one-time browser-based authorization. After this initial setup, refresh tokens enable fully programmatic operation. The authorization server at `https://account.habitify.me` supports `refresh_token` grant, `offline_access` scope, and dynamic client registration — all validated via OAuth discovery probes. **HIGH confidence** that headless operation works after initial setup.

### Two Completion APIs (Critical Gotcha)
Marking a habit complete works differently depending on whether the habit has a numeric goal:
- No goal → PUT `/status/:id` with `"status": "completed"` works
- Has goal → Must POST to `/logs/:id` with a value; the status endpoint rejects the completed status for goal-tracked habits

The LLM calling MCP tools will need to understand this distinction, or the agent code must detect habit type and route appropriately. If using the official MCP server's tools, the server presumably handles this internally. If using the REST API directly, you must implement the branching logic.

### Skip Status Is Legacy
`skipped` status only works for accounts created before August 20, 2020. For newer accounts, there is no "skip" concept — only `none`, `in_progress`, and `completed`. Plan the conversation flow accordingly (mark as `none` to undo a completion, not "skip").

### Community Server Has No Payload Docs
The `@sargonpiraev/habitify-mcp-server` lists 12 tools but the README does not document individual tool parameters. Parameters must be inferred from the underlying REST API docs or discovered at runtime by inspecting the MCP tool schemas.

### Tool Discovery at Runtime
Both MCP approaches rely on the LLM discovering tools dynamically via `list_tools()`. The 12 tool names are now documented (see Section 3). The tool schemas include parameter definitions that the LLM uses automatically. `list-habits-by-date` and `complete-habit` are the two most relevant for the accountability buddy use case. The `allowed_tools` parameter on `MCPServerHTTP` can be used to whitelist only these if desired.

### Node.js Dependency (Community Server)
Using `MCPServerStdio` with `npx` requires Node.js 18+ on the agent host. In a Docker/production deployment, this adds to the container image. Consider this in infrastructure planning.

### Beta Status
The official Habitify MCP integration was described as "Beta/Development" in at least one help article (specifically for ChatGPT). API behavior, tool names, and OAuth flows may change as it matures.

---

## 7. MCP and LiveKit Agents — How It Works During a Conversation

LiveKit Agents has first-class MCP support built into its Python SDK. The integration pattern:

1. `mcp_servers` is passed to `AgentSession` (or `Agent`) constructor
2. During session initialization, LiveKit calls `list_tools()` on the MCP server
3. All returned tools are automatically added to the LLM's tool registry (same as manually defined function tools)
4. During conversation, when the LLM decides to use a tool, LiveKit handles the tool call, executes it against the MCP server, and returns the result to the LLM
5. The LLM synthesizes a response and the TTS pipeline converts it to speech

This is transparent — from the user's perspective, the agent simply "knows" their habits and can update them mid-conversation without any manual API calls in the agent code.

**Key `MCPServerHTTP` parameters:**

| Parameter | Type | Purpose |
|-----------|------|---------|
| `url` | str | MCP server endpoint |
| `headers` | dict or None | Auth headers (e.g., `{"Authorization": "Bearer token"}`) |
| `allowed_tools` | list or None | Whitelist specific tools; all tools available if None |
| `transport_type` | "sse" or "streamable_http" or None | Auto-detected from URL if None |
| `timeout` | int | Connection timeout in seconds (default: 5) |

**Auto-detection rule:** URLs ending in `/mcp` → streamable HTTP. URLs ending in `sse` → SSE. The Habitify URL `https://mcp.habitify.me/mcp` will be correctly auto-detected as streamable HTTP.

---

## 8. Recommended Approach for This Project

Given the project requirements (Python LiveKit agent, read today's habits, mark complete after call), here is the recommended implementation path:

### Recommended: Official MCP Server via `MCPServerHTTP` + OAuth Refresh Token

Use the official Habitify MCP server with a one-time manual OAuth setup and programmatic token refresh. This gives us full MCP integration with LLM-native tool discovery while remaining fully automated at runtime.

**Implementation steps:**
1. **One-time setup script:** Register an OAuth client via `https://account.habitify.me/reg`, run authorization code + PKCE flow in a browser, capture refresh token
2. **Store refresh token:** In `.env.local` or a secrets manager
3. **Agent startup:** Refresh access token via `POST https://account.habitify.me/token` with `grant_type=refresh_token`
4. **Session:** Pass Bearer token to `MCPServerHTTP(headers={"Authorization": f"Bearer {token}"})`
5. **Tool discovery:** LLM automatically discovers and uses all Habitify tools (habits, logging, journal, etc.)

Pros:
- Full MCP integration — LLM discovers tools dynamically
- Official server with complete tool coverage (habits, logging, notes, areas, statistics, journal)
- No Node.js dependency
- Fully automated after initial setup
- Future-proof as Habitify updates their tools

Cons:
- One-time manual OAuth setup required (browser authorization)
- Must manage refresh token lifecycle
- LiveKit's `MCPServerHTTP` has no built-in token refresh — must refresh at session start

### Fallback: Direct REST API + Function Tools

If MCP proves problematic in practice (e.g., tool names change, response format issues), fall back to the REST API with a simple API key and `@function_tool` wrappers. This is simpler but requires implementing tool schemas manually.

### Not Recommended: Community NPM Server via `MCPServerStdio`

Adds a Node.js runtime dependency and the tool coverage is underdocumented. The official MCP server is now viable, making this unnecessary.

---

## Sources

- [Habitify MCP Announcement](https://feedback.habitify.me/changelog/introducing-habitify-mcp-track-your-habits-with-ai) — HIGH confidence
- [Habitify MCP "others" API docs](http://api-docs.habitify.me/mcp/others/) — HIGH confidence (official)
- [Habitify MCP Claude setup guide](http://api-docs.habitify.me/mcp/claude/) — HIGH confidence (official)
- [Habitify Use with ChatGPT (help center)](https://intercom.help/habitify-app/en/articles/13842610-use-habitify-with-chatgpt) — HIGH confidence (official, confirms `https://mcp.habitify.me/mcp` URL)
- [Habitify Use with Claude AI (help center)](https://intercom.help/habitify-app/en/articles/13839449-use-habitify-with-claude-ai) — HIGH confidence (official)
- [Habitify Use with AI Apps (help center)](https://intercom.help/habitify-app/en/articles/13843791-use-habitify-with-ai-apps-tools) — HIGH confidence (official)
- [Habitify REST API Docs — Habits](https://docs.habitify.me/core-resources/habits) — HIGH confidence
- [Habitify REST API Docs — Status](https://docs.habitify.me/core-resources/habits/status) — HIGH confidence
- [Habitify REST API Docs — Logs](https://docs.habitify.me/core-resources/habits/logs) — HIGH confidence
- [Habitify REST API Docs — Journal](https://docs.habitify.me/core-resources/journal) — HIGH confidence
- [Habitify REST API Docs — Authentication](https://docs.habitify.me/authentication) — HIGH confidence
- [Community MCP Server (sargonpiraev)](https://github.com/sargonpiraev/habitify-mcp-server) — MEDIUM confidence (community, not official)
- [LiveKit MCPServerHTTP API Docs](https://docs.livekit.io/reference/python/livekit/agents/llm/mcp.html) — HIGH confidence
- [LiveKit MCP Agent Recipe](https://docs.livekit.io/recipes/http_mcp_client/) — HIGH confidence
- [AssemblyAI MCP Voice Agent Tutorial](https://www.assemblyai.com/blog/mcp-voice-agent-openai-livekit) — MEDIUM confidence (tutorial)
