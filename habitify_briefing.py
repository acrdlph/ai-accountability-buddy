"""Pre-call reasoning agent that analyzes habit data via Habitify MCP.

Connects to the official Habitify MCP server, runs an agentic tool-calling
loop using OpenAI Responses API (gpt-5-mini), and produces a natural-language
briefing with patterns, streaks, and talking points for the accountability coach.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

import openai
from mcp import ClientSession
from mcp.client.sse import sse_client

logger = logging.getLogger("accountability-buddy")

HABITIFY_MCP_URL = "https://mcp.habitify.me/mcp"

MAX_ITERATIONS = 15

BRIEFING_SYSTEM_PROMPT = """\
You are a habit analyst preparing a briefing for an accountability coach who will \
use it during a voice call. Investigate the user's habit data and produce a concise briefing.

Steps:
1. Fetch today's habits to see what's due and what's done
2. Fetch the last 3-5 days to identify patterns
3. Produce a briefing

CRITICAL RULES:
- ONLY mention habits that appear in the fetched data. NEVER invent or assume habits.
- You MUST preserve the exact habit ID from the data for every habit. The IDs look like \
UUIDs in parentheses, e.g. (id: FE112B42-...). These IDs are required for logging completions later.
- Format each habit line as: "Habit Name (id: <full_uuid>) — status/progress"
- Include the date in ISO format (YYYY-MM-DD) next to today's habits — needed for tool calls.

Output format:

TODAY ({date}):
- Habit Name (id: <uuid>) — status (e.g. 0/2 reps done)
- ...

PATTERNS (last 3-5 days):
- Observations about streaks, slacking, wins

TALKING POINTS:
- What to push on, what to celebrate

Use the list-habits-by-date tool to gather data. Today's date is in the first user message.\
"""

# Only allow read tools in the briefing agent -- writes happen during the voice call
ALLOWED_BRIEFING_TOOLS = {"list-habits-by-date"}


def _mcp_tools_to_openai(mcp_tools: list) -> list[dict]:
    """Convert MCP tool schemas to OpenAI Responses API function tool format.

    Only includes tools in ALLOWED_BRIEFING_TOOLS (read-only for pre-call analysis).
    """
    openai_tools = []
    for tool in mcp_tools:
        if tool.name not in ALLOWED_BRIEFING_TOOLS:
            continue
        openai_tools.append({
            "type": "function",
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.inputSchema if tool.inputSchema else {"type": "object", "properties": {}},
        })
    return openai_tools


async def generate_briefing(access_token: str) -> str:
    """Generate a multi-day habit briefing via MCP agentic tool-calling loop.

    Connects to the Habitify MCP server, discovers tools, then runs an
    OpenAI Responses API loop where gpt-5-mini autonomously fetches and
    analyzes habit data across multiple days.

    Args:
        access_token: A valid Habitify OAuth access token.

    Returns:
        A natural-language briefing string with patterns, streaks, and
        talking points. Returns a fallback message on any failure.
    """
    try:
        return await _run_briefing_loop(access_token)
    except Exception as e:
        logger.error(f"Pre-call briefing failed: {e}")
        return "Could not connect to Habitify. Proceed with a general check-in."


def _build_structured_briefing(trace: list[dict], llm_analysis: str) -> str:
    """Build a concise, structured briefing from raw MCP data.

    The realtime voice model hallucinates habits when given long LLM narratives,
    so we build the habit list deterministically from MCP data and only use the
    LLM analysis for a short pattern summary.
    """
    import re

    today_date = date.today().isoformat()

    # Parse today's habits from the first tool call for today's date
    habits: list[dict] = []  # [{name, id, status_line}]
    seen_ids: set[str] = set()

    for entry in trace:
        if entry.get("tool") == "list-habits-by-date" and entry.get("arguments", {}).get("date") == today_date:
            raw = entry.get("result", "")
            for match in re.finditer(
                r"([\[x \-\]]+)\s*(.+?)\s*\(id:\s*([A-F0-9-]+)\):\s*(.+)",
                raw,
            ):
                status_marker, name, habit_id, progress = (
                    match.group(1).strip(),
                    match.group(2).strip(),
                    match.group(3),
                    match.group(4).strip(),
                )
                if habit_id not in seen_ids:
                    seen_ids.add(habit_id)
                    done = "x" in status_marker
                    habits.append({
                        "name": name,
                        "id": habit_id,
                        "progress": progress,
                        "done": done,
                    })

    if not habits:
        # Fallback: try to extract from any tool call
        for entry in trace:
            if entry.get("tool") == "list-habits-by-date":
                raw = entry.get("result", "")
                for match in re.finditer(
                    r"([\[x \-\]]+)\s*(.+?)\s*\(id:\s*([A-F0-9-]+)\)",
                    raw,
                ):
                    _, name, habit_id = match.group(1).strip(), match.group(2).strip(), match.group(3)
                    if habit_id not in seen_ids:
                        seen_ids.add(habit_id)
                        habits.append({"name": name, "id": habit_id, "progress": "", "done": False})

    if not habits:
        return llm_analysis  # No structured data, fall back to LLM

    # Build the concise briefing
    lines = [f"TODAY ({today_date}) — {len(habits)} habits:\n"]
    for h in habits:
        status = "DONE" if h["done"] else h["progress"]
        lines.append(f"- {h['name']} (habitId={h['id']}): {status}")

    # Extract a short pattern summary from the LLM analysis (first 500 chars max)
    # Strip markdown headers and keep only the insightful parts
    pattern_lines = []
    for line in llm_analysis.split("\n"):
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        if any(kw in stripped.lower() for kw in ["pattern", "streak", "slip", "consistent", "fail", "miss", "skip", "behind", "progress"]):
            pattern_lines.append(stripped)
    pattern_summary = " ".join(pattern_lines)[:500]

    if pattern_summary:
        lines.append(f"\nPATTERNS: {pattern_summary}")

    lines.append(f"\nUse habitId values above and date {today_date} when calling complete_habit or add_habit_log.")
    lines.append("These are the ONLY habits that exist. Do NOT mention any other habits.")

    return "\n".join(lines)


def _save_briefing_trace(trace: list[dict], briefing: str) -> None:
    """Save the briefing trace to a log file for debugging."""
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = log_dir / f"briefing_trace_{timestamp}.json"
    payload = {
        "timestamp": datetime.now().isoformat(),
        "trace": trace,
        "final_briefing": briefing,
    }
    filepath.write_text(json.dumps(payload, indent=2))
    logger.info(f"Briefing trace saved to {filepath}")


async def _run_briefing_loop(access_token: str) -> str:
    """Internal: connect to MCP, discover tools, run agentic loop."""
    headers = {"Authorization": f"Bearer {access_token}"}
    trace: list[dict] = []

    async with sse_client(HABITIFY_MCP_URL, headers=headers) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as mcp_session:
            await mcp_session.initialize()

            # Discover available tools
            tools_result = await mcp_session.list_tools()
            openai_tools = _mcp_tools_to_openai(tools_result.tools)

            if not openai_tools:
                logger.warning("No usable MCP tools discovered for briefing")
                return "Could not discover habit tools. Proceed with a general check-in."

            logger.info(f"Discovered {len(openai_tools)} MCP tools for briefing")

            # Run the agentic loop with OpenAI Responses API
            client = openai.AsyncOpenAI()

            response = await client.responses.create(
                model="gpt-5-mini",
                instructions=BRIEFING_SYSTEM_PROMPT,
                input=f"Today's date is {date.today().isoformat()}. Analyze my habit situation.",
                tools=openai_tools,
            )

            tool_call_count = 0
            iterations = 0

            while iterations < MAX_ITERATIONS:
                # Check for function calls in the output
                function_calls = [
                    item for item in response.output
                    if item.type == "function_call"
                ]

                if not function_calls:
                    break  # LLM produced final briefing (text output only)

                # Execute each tool call against the MCP server
                tool_results = []
                for fc in function_calls:
                    args = json.loads(fc.arguments)
                    try:
                        result = await mcp_session.call_tool(fc.name, args)
                        output_text = (
                            result.content[0].text
                            if result.content
                            else "No data"
                        )
                    except Exception as e:
                        logger.warning(f"MCP tool call {fc.name} failed: {e}")
                        output_text = f"Error calling {fc.name}: {e}"

                    trace.append({
                        "iteration": iterations,
                        "tool": fc.name,
                        "arguments": args,
                        "result": output_text,
                    })

                    tool_results.append({
                        "type": "function_call_output",
                        "call_id": fc.call_id,
                        "output": output_text,
                    })
                    tool_call_count += 1

                # Continue conversation with tool results -- history is server-side
                response = await client.responses.create(
                    model="gpt-5-mini",
                    previous_response_id=response.id,
                    input=tool_results,
                    tools=openai_tools,
                )
                iterations += 1

            # Extract final text from response output
            # The Responses API may return text as:
            #   1. Top-level output_text items (item.type == "output_text", item.text)
            #   2. Nested inside message items (item.type == "message", item.content[].type == "output_text")
            text_parts: list[str] = []
            for item in response.output:
                if hasattr(item, "text") and item.type == "output_text":
                    text_parts.append(item.text)
                elif item.type == "message" and hasattr(item, "content"):
                    for content_item in item.content:
                        if hasattr(content_item, "text") and content_item.type == "output_text":
                            text_parts.append(content_item.text)
            briefing = "\n".join(text_parts)

            logger.info(
                f"Briefing generated: {len(briefing)} chars, "
                f"{tool_call_count} tool calls, {iterations} iterations"
            )

            if not briefing:
                briefing = "Habit data was fetched but no briefing was produced. Proceed with a general check-in."

            # Build structured briefing from raw MCP data (deterministic habit list)
            briefing = _build_structured_briefing(trace, briefing)

            _save_briefing_trace(trace, briefing)
            return briefing
