# Habitify MCP Research

**Researched:** 2026-03-10
**Overall Confidence:** MEDIUM-HIGH (official docs found; OAuth headless flow for programmatic use is the key open question)

---

## 1. What Is the Habitify MCP Server

Habitify released an official MCP server that exposes their habit tracking data and actions to AI assistants. It uses the Model Context Protocol standard, making it compatible with Claude, ChatGPT, Cursor, and any other MCP-compliant client.

**Official MCP endpoint:**
```
https://mcp.habitify.me/mcp
```

**Transport:** HTTP Streamable (confirmed in official "others" docs at `api-docs.habitify.me/mcp/others`). Note: one Habitify help page described it as "SSE (Server-Sent Events)" — the machine-readable docs say "HTTP (Streamable)", which is the newer transport. Treat it as streamable HTTP; LiveKit's `MCPServerHTTP` auto-detects based on URL ending in `/mcp`, which maps to streamable HTTP.

**Rate limit:** 500 requests per minute per account (confirmed in official MCP API docs).

**Source confidence:** HIGH — official Habitify MCP docs at `api-docs.habitify.me/mcp/others` and help articles at `intercom.help/habitify-app`.

---

## 2. Authentication

### Official MCP Server (`mcp.habitify.me`)

The official server uses **OAuth 2.0 with dynamic client registration**. There is no static API key option for the MCP endpoint.

- Auth flow: OAuth 2.0 authorization code with user consent
- On first connection, the user is redirected to log in to their Habitify account and authorize access
- This is designed for interactive browser-based flows (Claude Desktop, ChatGPT, etc.)

**Headless/programmatic concern (LOW confidence — needs validation):** The OAuth flow requires a browser redirect for initial authorization. For a server-side Python agent running as a background process, this is a friction point. The token obtained after the initial OAuth dance would need to be stored and refreshed. The MCP spec supports `client_credentials` grant for machine-to-machine flows, but it is unclear whether Habitify's OAuth server supports this grant type — their docs only describe user-facing flows. This is the single biggest unknown for this project.

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

### Official MCP Server Tools (Functional Categories — Confirmed)

The official server (`mcp.habitify.me`) exposes tools covering:

| Category | Capabilities |
|----------|-------------|
| Habits | Create, list, update, archive, delete |
| Logging | Mark complete/failed/skipped; log numeric values; undo |
| Notes | Add, modify, remove annotations |
| Areas | Manage organizational categories |
| Statistics | Completion metrics, streak data |
| Journal | Daily habit overview with progress tracking |

Exact tool names for the official server are not publicly documented (unlike the community server). The LLM discovers them at runtime via the MCP protocol's `list_tools()` call.

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

**Notable gap:** Neither server has a community-documented `update-status` or `mark-complete` tool with explicit parameter schemas. The community server's `add-habit-log` is the write mechanism for goal-based habits. The official server's "Logging" category covers mark-complete flows.

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

### Option A: Official MCP Server via `MCPServerHTTP` (requires OAuth token)

LiveKit's `MCPServerHTTP` accepts a `headers` dict for authentication. If you can obtain and store an OAuth bearer token from Habitify, you can pass it directly:

```python
from livekit.agents.llm import mcp

session = AgentSession(
    ...
    mcp_servers=[
        mcp.MCPServerHTTP(
            url="https://mcp.habitify.me/mcp",
            headers={"Authorization": f"Bearer {habitify_oauth_token}"},
        )
    ],
)
```

The LLM automatically discovers and calls all tools exposed by the server — no manual tool registration needed.

**Blocker:** Obtaining `habitify_oauth_token` programmatically requires completing the OAuth flow. In a headless server context this likely means: (a) complete the OAuth flow once manually via browser, (b) capture and store the access + refresh tokens, (c) use a refresh mechanism before each session. This is standard OAuth but adds implementation complexity. Whether Habitify's token endpoint supports refresh_token grant must be validated by attempting the flow.

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

### Authentication Friction for Headless Use
The official MCP server requires OAuth, which is designed for interactive user consent. A background voice agent is not an interactive context. The token must be pre-obtained and managed. Whether Habitify's OAuth server exposes a `/token` endpoint supporting `refresh_token` grants is undocumented — must be validated empirically. **LOW confidence** that headless operation is straightforward with the official MCP server.

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
Both MCP approaches rely on the LLM discovering tools dynamically via `list_tools()`. The specific tool names exposed by the official server (`mcp.habitify.me`) are not publicly listed. This means you cannot hard-code tool names or parameters in the agent — the LLM must navigate the schema. This is fine for general assistant use but requires careful prompt engineering to ensure the agent calls the right tools reliably.

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

Given the project requirements (Python LiveKit agent, read today's habits, mark complete after call), here is the recommended implementation path ranked by pragmatism:

### Recommended: Direct REST API + Function Tools (bypass MCP)

Use Habitify's REST API with the simple API key directly in the LiveKit agent as regular `@function_tool` decorated functions. This avoids the OAuth complexity of the official MCP server and the Node.js dependency of the community server.

Pros:
- Simple API key auth (no OAuth dance)
- Full control over data model and error handling
- No external process dependency
- Fully documented endpoints

Cons:
- Not using MCP (may matter for future extensibility)
- Requires implementing the goal-vs-no-goal branching manually

### Fallback: Community NPM Server via `MCPServerStdio`

If MCP integration is desired for LLM-native tool discovery, use the community server via stdio. Requires Node.js on the agent host.

### Avoid (for now): Official `mcp.habitify.me` Server

The OAuth requirement for headless operation is unvalidated. Until Habitify documents a machine-to-machine auth path or refresh token flow, avoid blocking the project on this. Revisit when the OAuth flow is better understood.

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
