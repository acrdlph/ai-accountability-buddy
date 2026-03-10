# Phase 3: Habitify Integration - Research

**Researched:** 2026-03-10
**Domain:** Habitify REST API integration + LiveKit Agents metadata injection + function tool design for habit logging
**Confidence:** HIGH

---

## Summary

Phase 3 connects the accountability agent to the user's real Habitify data. The work splits into two distinct parts: (1) a pre-call data fetch that pulls today's habits and their completion status from the Habitify Journal API at dispatch time, injecting them into the agent's instructions via dispatch metadata, and (2) a `log_habit` function tool that the agent calls during the conversation to mark habits as complete or log numeric progress.

The Habitify REST API is straightforward -- API key auth via `Authorization` header, JSON responses, ISO-8601 dates with timezone offset. The critical API distinction is between simple habits (no `goal` object -- mark complete via `PUT /status/:habit_id`) and goal-based habits (have a `goal` object with `unit_type` and `value` -- log progress via `POST /logs/:habit_id`). The Journal endpoint (`GET /journal?target_date=...`) returns both habit metadata and current completion status in a single call, making it the ideal pre-call data source.

On the LiveKit side, the pattern is well-established: parse `ctx.job.metadata` in the entrypoint, construct the agent with dynamic instructions that include the habit list, and set `max_tool_steps` on `AgentSession` to accommodate one tool call per habit plus overhead. The survey caller recipe demonstrates this exact metadata-to-instructions pattern. The existing `agent.py` already parses metadata for phone numbers -- extending it to include habit data is a natural evolution. The `aiohttp` library is already a transitive dependency of `livekit-agents`, so no new HTTP client package is needed for API calls.

**Primary recommendation — Two-Stage Pre-Call Architecture (USER DECISION):**

The user explicitly requested a **pre-call reasoning LLM** instead of a simple API fetch. The architecture is:

1. **Stage 1: Pre-call reasoning LLM** — Before the voice call starts, spawn a lightweight LLM call (e.g., GPT-4o-mini or similar) that:
   - Calls the Habitify Journal API for **today AND the last few days** (not just today)
   - Reasons through the habit data: what's due today, what's overdue, streaks broken, patterns of slacking
   - Produces a **structured briefing** — a summary of the user's habit situation with talking points
   - This is NOT a simple fetch-and-format — it's an LLM that uses tools to gather data and then reasons about what's important to discuss

2. **Stage 2: Voice agent** — Receives the briefing as injected context in its system prompt. Knows exactly what to ask about, what to push on (e.g., "you've skipped meditation 3 days in a row"), what to celebrate. Does NOT need to call Habitify read APIs itself.

3. **Write side stays on voice agent** — The `log_habit` function tool remains on the voice agent for real-time habit completion during the conversation.

This replaces the simpler "fetch in entrypoint, format into prompt" pattern. The pre-call LLM adds intelligence — it can identify patterns, prioritize what to discuss, and produce natural-language context rather than a raw habit list.

**Implementation approach:** Use the OpenAI chat completions API (already available via `openai` package) for the pre-call reasoning step. Give it the Habitify API as tools (function calling). Run it in the entrypoint before creating the voice agent. Pass its output as part of the `AccountabilityAgent` instructions.

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| FR2 | Habit Awareness -- before the call, agent fetches today's habits and their current completion status from Habitify REST API | `GET /journal?target_date=<today>` returns habit list with `status` and `progress` fields; fetched in entrypoint, injected into agent instructions via constructor; pattern verified in LiveKit survey caller recipe |
| FR4 | Automatic Habit Tracking -- agent marks habits complete or logs progress in Habitify based on conversation, handling both simple (yes/no) and goal-based (numeric) habits | `PUT /status/:habit_id` with `{"status":"completed"}` for simple habits; `POST /logs/:habit_id` with `{"value":N, "unit_type":"...", "target_date":"..."}` for goal-based habits; Habitify API explicitly documents this branching requirement |
</phase_requirements>

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| livekit-agents[openai] | ~=1.4 (installed) | Agent framework with `function_tool`, `AgentSession`, `max_tool_steps` | Already installed; provides all tool and session infrastructure |
| aiohttp | 3.x (transitive dep) | HTTP client for Habitify REST API calls | Already a transitive dependency of `livekit-agents`; async-native; no new package needed |
| Habitify REST API | v1.2.2 | Habit data source -- journal, status, logs endpoints | Project requirement; API key auth, JSON responses |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| python-dotenv | >=1.0 (installed) | Load `HABITIFY_API_KEY` from `.env.local` | Already installed; used for all env vars |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| aiohttp (transitive dep) | httpx | httpx would need explicit install; aiohttp is already available and works fine for simple REST calls |
| Direct Habitify REST calls | Habitify MCP OAuth | Project decision: REST API is simpler, avoids headless OAuth complexity (documented in STATE.md) |
| Pre-call fetch in entrypoint | Fetch inside a tool at conversation start | Pre-call fetch is faster (no tool call overhead), data is in the prompt from the first utterance, matches user requirement that habits are fetched "at dispatch time" |

**Installation:**

```bash
# No new packages needed. aiohttp is already a transitive dependency.
# Only add the env var:
# HABITIFY_API_KEY=<your-api-key>  (to .env.local)
```

---

## Architecture Patterns

### Recommended Project Structure

```
accountability-buddy/
├── .env.local              # Add: HABITIFY_API_KEY
├── pyproject.toml          # No changes needed
├── agent.py                # Modify: add habitify fetch + log_habit tool + dynamic instructions
└── habitify.py             # NEW: Habitify REST client (thin async wrapper)
```

A separate `habitify.py` file keeps the REST client isolated from agent logic. This is the first time the project adds a second Python file, but the Habitify client is a distinct concern (HTTP calls, date formatting, response parsing) that would clutter `agent.py`.

### Pattern 1: Pre-Call Habit Fetch via Journal API

**What:** In the entrypoint function, before creating the agent, call the Habitify Journal API to get today's habits and their status. Pass this data into the `AccountabilityAgent` constructor, which formats it into the system prompt.

**When to use:** Always -- habits must be in the prompt before `on_enter` fires.

**Example:**

```python
# Source: Habitify API docs (https://docs.habitify.me/core-resources/journal)
# + LiveKit survey caller recipe pattern (https://docs.livekit.io/recipes/survey_caller/)

import aiohttp
from datetime import datetime, timezone, timedelta

HABITIFY_BASE = "https://api.habitify.me"

async def fetch_today_habits(api_key: str, tz_offset_hours: int = 1) -> list[dict]:
    """Fetch today's habits from Habitify Journal API."""
    tz = timezone(timedelta(hours=tz_offset_hours))
    now = datetime.now(tz)
    target_date = now.strftime("%Y-%m-%dT%H:%M:%S") + f"+{tz_offset_hours:02d}:00"

    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{HABITIFY_BASE}/journal",
            headers={"Authorization": api_key},
            params={"target_date": target_date},
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data.get("data", [])
```

### Pattern 2: Habit List Injection into Agent Instructions

**What:** The `AccountabilityAgent` constructor accepts a list of habit dicts and formats them into the system prompt. Each habit includes its name, type (simple/goal), current status, and goal details if applicable.

**When to use:** Always -- the agent needs habit context to conduct the check-in.

**Example:**

```python
# Pattern from: LiveKit prompting guide (https://docs.livekit.io/agents/start/prompting/)
# "User information" section recommends injecting context into instructions

class AccountabilityAgent(Agent):
    def __init__(self, habits: list[dict]) -> None:
        self.habits = habits
        habit_lines = self._format_habits(habits)

        instructions = SYSTEM_PROMPT + f"""

Today's habits:
{habit_lines}

Review each habit with the user. For completed habits, briefly acknowledge.
For incomplete habits, challenge the user. When they confirm a habit is done,
use the log_habit tool to record it. For goal-based habits, ask for the
specific number before logging."""

        end_call_tool = EndCallTool(
            delete_room=True,
            end_instructions="Say a direct, firm goodbye. No fluff.",
        )
        super().__init__(
            instructions=instructions,
            tools=end_call_tool.tools,
        )

    @staticmethod
    def _format_habits(habits: list[dict]) -> str:
        lines = []
        for h in habits:
            status = h.get("status", "none")
            name = h["name"]
            goal = h.get("goal")
            if goal:
                lines.append(
                    f"- {name} (goal: {goal['value']} {goal['unit_type']}/day) "
                    f"[status: {status}]"
                )
            else:
                lines.append(f"- {name} (yes/no) [status: {status}]")
        return "\n".join(lines)
```

### Pattern 3: Branching log_habit Tool (Simple vs Goal-Based)

**What:** A single `log_habit` function tool that the agent calls to record completion. It branches internally: for simple habits (no goal), it calls `PUT /status/:id`; for goal-based habits, it calls `POST /logs/:id` with the numeric value.

**When to use:** During the conversation when the user confirms a habit is done.

**Example:**

```python
# Sources:
# PUT /status/:id -- https://docs.habitify.me/core-resources/habits/status
# POST /logs/:id -- https://docs.habitify.me/core-resources/habits/logs

@function_tool()
async def log_habit(
    self,
    context: RunContext,
    habit_name: str,
    value: float | None = None,
) -> str:
    """Log a habit as completed. For goal-based habits, include the numeric value.

    Args:
        habit_name: The name of the habit to log.
        value: For goal-based habits, the numeric value achieved. Not needed for simple yes/no habits.
    """
    habit = self._find_habit(habit_name)
    if not habit:
        return f"Could not find habit '{habit_name}'"

    if habit.get("goal"):
        # Goal-based: POST /logs/:id with value
        if value is None:
            return "This is a goal-based habit. Please ask the user for the specific number."
        result = await habitify_add_log(
            api_key=self.api_key,
            habit_id=habit["id"],
            value=value,
            unit_type=habit["goal"]["unit_type"],
            target_date=self.target_date,
        )
    else:
        # Simple: PUT /status/:id with completed
        result = await habitify_update_status(
            api_key=self.api_key,
            habit_id=habit["id"],
            status="completed",
            target_date=self.target_date,
        )

    return f"Logged {habit_name} as complete" if result else f"Failed to log {habit_name}"
```

### Pattern 4: max_tool_steps Configuration

**What:** Set `max_tool_steps` on `AgentSession` to allow the agent to make enough consecutive tool calls to handle all habits in a single turn without hitting the step limit.

**When to use:** Always. The default is 3, which is insufficient for 5+ habits.

**Example:**

```python
# Source: LiveKit docs (https://docs.livekit.io/agents/logic/sessions/)
# "max_tool_steps: Maximum consecutive tool calls per LLM turn. Default: 3."
# Also: drive-thru example uses max_tool_steps=10

session = AgentSession(
    llm=openai.realtime.RealtimeModel(voice="shimmer"),
    max_tool_steps=10,  # 5 habits + overhead for end_call + voicemail detection
)
```

### Pattern 5: Dispatch Metadata Extension

**What:** Extend the dispatch metadata to include the habit list fetched from Habitify, so the agent has it at startup.

**When to use:** This is an ALTERNATIVE to Pattern 1. However, Pattern 1 (fetch in entrypoint) is simpler because: (a) the entrypoint already has access to env vars, (b) metadata size limits could be a concern with many habits, and (c) the fetch is a few hundred ms which is invisible during the SIP dial delay.

**Recommendation:** Fetch in the entrypoint, NOT in the dispatch caller. The entrypoint runs inside the agent process and has direct access to `HABITIFY_API_KEY`. This avoids passing the API key through metadata and keeps the dispatch command simple.

### Anti-Patterns to Avoid

- **Fetching habits inside a function tool instead of at startup:** The agent would start the call without knowing what habits to review. It would need to call a tool before speaking, adding latency and an awkward pause.
- **Using a single API endpoint for both simple and goal-based habits:** The Habitify API explicitly requires `PUT /status/:id` for simple habits and `POST /logs/:id` for goal-based. Sending the wrong request type will fail. The status endpoint documentation states: "If you want to complete the habit that has a goal. You should use Add Log to log your habit's progress."
- **Hardcoding habit IDs:** Habit IDs come from the API at runtime. Never hardcode them.
- **Setting max_tool_steps too low:** Default is 3. With 5+ habits, the agent will be blocked from logging all of them in a single conversation turn. Set to at least `len(habits) + 2`.
- **Forgetting URL encoding for dates in query parameters:** The Habitify API requires ISO-8601 dates with timezone offsets. The `+` in `+07:00` must be URL-encoded as `%2B` in query strings. Using `aiohttp`'s `params` dict handles this automatically.
- **Skipping habits the user says they didn't do:** The agent should NOT call `log_habit` for skipped habits. Leaving them with status `none` or `in_progress` is correct -- do not set them to `skipped` status (that's a deliberate user action in the Habitify app, not the same as "didn't do it today").

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| HTTP client for API calls | Custom socket/urllib code | `aiohttp.ClientSession` (already available) | Async, connection pooling, proper timeout handling, URL encoding |
| Date formatting with timezone | Manual string concatenation | `datetime` with `timezone`/`timedelta` | Handles DST edge cases, proper ISO-8601 formatting |
| Habit type detection | String parsing of habit names | Check `habit.get("goal") is not None` | The `goal` field presence is the authoritative indicator; no guessing |
| Retry logic for API calls | Custom retry loop | Single attempt with error handling | Habitify API is reliable; a single call during a phone conversation is fine; retries add complexity |

**Key insight:** The Habitify REST client is intentionally thin -- three async functions (fetch journal, update status, add log) wrapping three HTTP calls. There's no SDK to install because the API is simple enough that raw `aiohttp` calls are cleaner than any wrapper library.

---

## Common Pitfalls

### Pitfall 1: Wrong API Endpoint for Goal-Based Habits

**What goes wrong:** Agent calls `PUT /status/:id` with `{"status":"completed"}` for a goal-based habit. The API silently accepts it but the habit shows as "completed" without the actual progress value. The user's tracking data is corrupted.

**Why it happens:** The Habitify API documentation explicitly states: "If you want to complete the habit that has a goal. You should use Add Log to log your habit's progress." But the status endpoint doesn't reject the request for goal-based habits -- it just doesn't record the numeric value.

**How to avoid:** Branch on `habit.get("goal")` presence. If `goal` exists, use `POST /logs/:id`. If not, use `PUT /status/:id`. This check MUST happen in the tool implementation, not in the prompt.

**Warning signs:** Habits show as "completed" in Habitify but with 0 or no progress value. User reports that their goal tracking numbers are missing.

### Pitfall 2: Date Format Errors

**What goes wrong:** API returns errors or wrong-day data because the `target_date` parameter doesn't include the timezone offset.

**Why it happens:** Habitify requires ISO-8601 with timezone offset: `2021-05-21T07:00:00+07:00`. Omitting the timezone or using a bare date like `2021-05-21` may return unexpected results or errors.

**How to avoid:** Always format dates as `yyyy-MM-ddTHH:mm:ss+HH:MM`. Use Python's `datetime.now(tz)` with an explicit timezone. URL-encode the `+` as `%2B` in query params (aiohttp's `params` dict does this automatically).

**Warning signs:** Agent talks about yesterday's habits or habits from a different day. API returns empty data unexpectedly.

### Pitfall 3: max_tool_steps Blocking Completion

**What goes wrong:** The agent logs the first 2-3 habits but then stops calling the `log_habit` tool for the remaining ones. The conversation seems to end prematurely or the agent summarizes without logging.

**Why it happens:** `AgentSession` default `max_tool_steps=3`. After 3 consecutive tool calls in a single LLM turn, the framework stops executing tools and forces a text response. The warning log says "max tool steps reached."

**How to avoid:** Set `max_tool_steps` on `AgentSession` constructor. Use `len(habits) + 2` as a minimum, or a fixed value like 10 which handles up to 8 habits comfortably.

**Warning signs:** Agent logs in the LiveKit dashboard show "max tool steps reached" warnings. Habitify shows some habits logged but not all.

### Pitfall 4: Habit Name Fuzzy Matching Failures

**What goes wrong:** The agent says "I'll log your meditation" but the tool receives `habit_name="meditation"` and the actual Habitify habit is named "Morning Meditation (10 min)". The tool fails to find a match.

**Why it happens:** The LLM abbreviates or paraphrases habit names. Exact string matching fails.

**How to avoid:** Use case-insensitive substring matching in `_find_habit()`. If the LLM sends "meditation", match it to any habit containing "meditation". If ambiguous, return an error asking the LLM to be more specific. Consider also providing habit IDs to the LLM in the prompt so it can pass the ID directly.

**Warning signs:** Tool returns "Could not find habit" errors in the logs. User reports habits weren't logged despite confirming them verbally.

### Pitfall 5: API Key Not Set or Invalid

**What goes wrong:** Agent starts the call but all Habitify API calls fail with 401. The agent has no habits to discuss and fumbles the conversation.

**Why it happens:** `HABITIFY_API_KEY` not set in `.env.local`, or the key is expired/invalid.

**How to avoid:** Validate the API key at startup (attempt a test fetch). If the key is missing or the test fetch fails, log an error and shut down gracefully -- don't start a phone call with no habit data.

**Warning signs:** Agent starts talking but doesn't mention any specific habits. Logs show 401 errors from Habitify API.

### Pitfall 6: Habits Already Completed Before the Call

**What goes wrong:** The agent asks "did you do X?" for a habit that's already marked complete in Habitify (e.g., logged via Apple Health sync). The user is confused or annoyed.

**Why it happens:** The Journal API returns ALL habits for the day, including those already completed.

**How to avoid:** Include the `status` field in the formatted habit list. The prompt tells the agent to acknowledge already-completed habits briefly without asking about them. The agent should say something like "I see you already got your meditation in -- nice" and move on.

**Warning signs:** User says "I already did that!" repeatedly during the call.

---

## Code Examples

### Complete Habitify REST Client (habitify.py)

```python
# Source: Habitify API docs
# https://docs.habitify.me/core-resources/journal
# https://docs.habitify.me/core-resources/habits/status
# https://docs.habitify.me/core-resources/habits/logs

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

import aiohttp

logger = logging.getLogger("accountability-buddy")

HABITIFY_BASE = "https://api.habitify.me"


def _format_target_date(tz_offset_hours: int) -> str:
    """Format current time as ISO-8601 with timezone offset for Habitify API."""
    tz = timezone(timedelta(hours=tz_offset_hours))
    now = datetime.now(tz)
    sign = "+" if tz_offset_hours >= 0 else "-"
    return now.strftime(f"%Y-%m-%dT%H:%M:%S{sign}{abs(tz_offset_hours):02d}:00")


async def fetch_journal(api_key: str, tz_offset_hours: int = 1) -> list[dict]:
    """Fetch today's habits with status from Habitify Journal API.

    Returns list of habit dicts with keys: id, name, status, goal, progress, etc.
    """
    target_date = _format_target_date(tz_offset_hours)
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{HABITIFY_BASE}/journal",
            headers={"Authorization": api_key},
            params={"target_date": target_date},
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data.get("data", [])


async def update_status(
    api_key: str,
    habit_id: str,
    status: str,
    target_date: str,
) -> bool:
    """Update habit status (for simple yes/no habits).

    PUT /status/:habit_id
    Body: {"status": "completed"|"none", "target_date": "..."}
    """
    async with aiohttp.ClientSession() as session:
        async with session.put(
            f"{HABITIFY_BASE}/status/{habit_id}",
            headers={"Authorization": api_key},
            json={"status": status, "target_date": target_date},
        ) as resp:
            if resp.status == 200:
                return True
            logger.error(f"Failed to update status for {habit_id}: {resp.status}")
            return False


async def add_log(
    api_key: str,
    habit_id: str,
    value: float,
    unit_type: str,
    target_date: str,
) -> bool:
    """Add a log entry (for goal-based habits).

    POST /logs/:habit_id
    Body: {"value": N, "unit_type": "...", "target_date": "..."}
    """
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{HABITIFY_BASE}/logs/{habit_id}",
            headers={"Authorization": api_key},
            json={
                "value": value,
                "unit_type": unit_type,
                "target_date": target_date,
            },
        ) as resp:
            if resp.status == 200:
                return True
            logger.error(f"Failed to add log for {habit_id}: {resp.status}")
            return False
```

### Entrypoint Modification (in agent.py)

```python
# Source: pattern from LiveKit survey caller recipe
# https://docs.livekit.io/recipes/survey_caller/

from habitify import fetch_journal, _format_target_date

habitify_api_key = os.getenv("HABITIFY_API_KEY")
tz_offset = int(os.getenv("TZ_OFFSET_HOURS", "1"))  # CET = +1

async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    # Parse phone number from dispatch metadata
    metadata = json.loads(ctx.job.metadata or "{}")
    phone_number = metadata.get("phone") or metadata.get("phone_number") or default_phone
    if not phone_number:
        logger.error("No phone number provided")
        ctx.shutdown()
        return

    # Fetch today's habits BEFORE creating the agent
    habits = []
    if habitify_api_key:
        try:
            habits = await fetch_journal(habitify_api_key, tz_offset)
            logger.info(f"Fetched {len(habits)} habits from Habitify")
        except Exception as e:
            logger.error(f"Failed to fetch habits: {e}")
    else:
        logger.warning("HABITIFY_API_KEY not set -- running without habit data")

    target_date = _format_target_date(tz_offset)

    session = AgentSession(
        llm=openai.realtime.RealtimeModel(voice="shimmer"),
        max_tool_steps=max(len(habits) + 2, 5),  # ensure enough steps for all habits
    )

    session_started = asyncio.create_task(
        session.start(
            agent=AccountabilityAgent(
                habits=habits,
                api_key=habitify_api_key,
                target_date=target_date,
            ),
            room=ctx.room,
            room_input_options=RoomInputOptions(
                noise_cancellation=noise_cancellation.BVCTelephony(),
            ),
        )
    )

    # ... SIP dial continues as before ...
```

### Dispatch Command for Testing

```bash
# Phase 3 dispatch -- same as Phase 2, habits fetched automatically in entrypoint
lk dispatch create \
  --new-room \
  --agent-name accountability-buddy \
  --metadata '{"phone":"+491712740148"}'
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Habitify API v1.0/v1.1 URLs | v1.2 restructured URLs for status, logs, notes | 2021-06-04 | Status at `/status/:id`, Logs at `/logs/:id`, Journal is new resource |
| No Journal endpoint | `GET /journal` with status filtering | v1.2 (2021-06-04) | Single endpoint returns habits + status for a given day; eliminates need for multiple calls |
| `@llm.ai_callable` (LiveKit v0.x) | `@function_tool` (LiveKit Agents 1.x) | 2025 | New decorator name; same concept, different import path |
| No `max_tool_steps` config | `AgentSession(max_tool_steps=N)` | LiveKit Agents 1.x | Explicit control over consecutive tool calls per LLM turn |

**Deprecated/outdated:**
- Habitify API `habit_id` field in Log object: marked as deprecated in docs, phased out. Use the path parameter `:habit_id` instead.
- LiveKit `@llm.ai_callable`: replaced by `@function_tool` in Agents 1.x.

---

## Open Questions

1. **Habitify API rate limits**
   - What we know: The API docs don't mention explicit rate limits. The GitHub client library handles 429 responses.
   - What's unclear: Whether there's a per-minute or per-day limit that could affect logging 5+ habits in rapid succession during a call.
   - Recommendation: Test empirically with real account. If 429s occur, add a small delay between `log_habit` calls (unlikely to be needed for 5 habits).

2. **Goal-based habit completion threshold**
   - What we know: `POST /logs/:id` adds a log entry with a value. If the cumulative value meets the goal target, Habitify auto-marks the habit as "completed."
   - What's unclear: Whether a single log entry that meets/exceeds the target value automatically completes the habit, or whether the user needs to have the full daily target logged.
   - Recommendation: Test with a real goal-based habit. Log a value equal to the target and verify the status changes to "completed" in the Habitify app.

3. **Timezone offset configuration**
   - What we know: The user is in Germany (phone number +49...). CET is UTC+1, CEST is UTC+2.
   - What's unclear: Whether to hardcode the timezone offset or make it configurable. DST transitions could cause wrong-day habit fetches.
   - Recommendation: Use `TZ_OFFSET_HOURS` env var for now. Phase 4 (scheduling) will need proper timezone handling with `zoneinfo` for DST awareness. For Phase 3, a simple offset is sufficient since the user manually dispatches calls.

4. **Habit name matching reliability**
   - What we know: The LLM will paraphrase habit names. Exact string matching will fail.
   - What's unclear: How much the LLM will abbreviate or modify names.
   - Recommendation: Include habit IDs in the prompt alongside names. Have the tool accept either name (fuzzy match) or ID (exact match). Prioritize ID-based matching if the LLM cooperates.

---

## Sources

### Primary (HIGH confidence)

- Habitify Journal API -- `GET /journal` with `target_date`, response structure with habit status and progress
  - URL: https://docs.habitify.me/core-resources/journal
- Habitify Status API -- `GET /status/:id`, `PUT /status/:id` with `status` and `target_date` body fields
  - URL: https://docs.habitify.me/core-resources/habits/status
  - Critical note: "If you want to complete the habit that has a goal. You should use Add Log"
- Habitify Logs API -- `GET /logs/:id`, `POST /logs/:id` with `value`, `unit_type`, `target_date` body fields
  - URL: https://docs.habitify.me/core-resources/habits/logs
- Habitify Date Format -- ISO-8601 with timezone offset: `yyyy-MM-ddTHH:mm:ss+HH:MM`, URL-encode `+` as `%2B`
  - URL: https://docs.habitify.me/date-format
- Habitify Authentication -- API key in `Authorization` header
  - URL: https://docs.habitify.me/authentication
- Habitify Goal Object -- `unit_type`, `value`, `periodicity` fields
  - URL: https://docs.habitify.me/core-resources/habits/goal
- Habitify Unit Type Enum -- 24 values: kM, m, ft, yd, mi, L, mL, fl oz, cup, kg, g, mg, oz, lb, mcg, sec, min, hr, J, kJ, kCal, cal, rep
  - URL: https://docs.habitify.me/enum/unit-type
- LiveKit Agent Session docs -- `max_tool_steps` parameter, default 3, set on `AgentSession` constructor
  - URL: https://docs.livekit.io/agents/logic/sessions/
- LiveKit Function Tool docs -- `@function_tool` decorator, `RunContext`, return values, error handling
  - URL: https://docs.livekit.io/agents/logic/tools/
- LiveKit Prompting Guide -- user information injection via job metadata, structured instructions
  - URL: https://docs.livekit.io/agents/start/prompting/
- LiveKit Agent Dispatch docs -- `ctx.job.metadata` for passing structured data, JSON recommended
  - URL: https://docs.livekit.io/agents/server/agent-dispatch/
- LiveKit Survey Caller Recipe -- complete example of metadata parsing, dynamic agent instructions, function tool for recording data
  - URL: https://docs.livekit.io/recipes/survey_caller/
- LiveKit `max_tool_steps` code search -- confirmed in `agent_session.py`, examples use 5-10
  - URL: https://github.com/livekit/agents (code search: `max_tool_steps`)

### Secondary (MEDIUM confidence)

- Habitify API Changelog -- current version 1.2.2, last updated 2021-07-30; Journal endpoint added in v1.2
  - URL: https://docs.habitify.me/change-log
- Habitify GitHub client (sargonpiraev/habitify-api-client) -- TypeScript reference showing endpoint patterns, date format `YYYY-MM-DD` in examples, confirms `target_date` usage
  - URL: https://github.com/sargonpiraev/habitify-api-client
- Habitify Log Method Enum -- `manual`, `appleHealth`, `googleFit`, `samsungHealth` (not relevant to habit type detection)
  - URL: https://docs.habitify.me/enum/log-method

### Tertiary (LOW confidence)

- None -- all patterns verified against official documentation.

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new packages; aiohttp already available; Habitify API is simple REST
- Architecture patterns: HIGH -- metadata-to-instructions pattern verified in LiveKit survey caller recipe; function tool branching matches Habitify API docs exactly
- Pitfalls: HIGH -- the simple/goal branching requirement is explicitly documented by Habitify; max_tool_steps confirmed in LiveKit source code
- Habitify API: HIGH -- all endpoints verified against official docs; date format confirmed; status endpoint caveat about goal-based habits documented
- Habit name matching: MEDIUM -- fuzzy matching strategy is sound but LLM behavior is empirical

**Research date:** 2026-03-10
**Valid until:** 2026-04-10 (Habitify API is stable at v1.2.2 since 2021; LiveKit Agents SDK stable at 1.4.x)
