"""Pre-call reasoning agent that analyzes habit data via Habitify MCP.

Connects to the official Habitify MCP server, runs an agentic tool-calling
loop using OpenAI Responses API (gpt-4o-mini), and produces a natural-language
briefing with patterns, streaks, and talking points for the accountability coach.
"""

from __future__ import annotations

import json
import logging
from datetime import date

import openai
from mcp import ClientSession
from mcp.client.sse import sse_client

logger = logging.getLogger("accountability-buddy")

HABITIFY_MCP_URL = "https://mcp.habitify.me/mcp"

MAX_ITERATIONS = 15

BRIEFING_SYSTEM_PROMPT = """\
You are a habit analyst preparing a briefing for an accountability coach.
Your job: investigate the user's habit data and produce a concise briefing.

Steps:
1. Fetch today's habits to see what's due and what's done
2. Fetch the last 3-5 days to identify patterns
3. Look for: broken streaks, habits being slacked on, consistent wins
4. Produce a briefing with:
   - List of today's habits with status (due, completed, in_progress)
   - Pattern observations (e.g. "skipped meditation 3 days in a row")
   - Suggested talking points for the accountability coach
   - Which habits to celebrate, which to push on

Use the list-habits-by-date tool with different dates to gather data.
Today's date is provided in the first user message.
When you have enough data, produce your final briefing.\
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
    OpenAI Responses API loop where gpt-4o-mini autonomously fetches and
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


async def _run_briefing_loop(access_token: str) -> str:
    """Internal: connect to MCP, discover tools, run agentic loop."""
    headers = {"Authorization": f"Bearer {access_token}"}

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
                model="gpt-4o-mini",
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
                    try:
                        result = await mcp_session.call_tool(
                            fc.name,
                            json.loads(fc.arguments),
                        )
                        output_text = (
                            result.content[0].text
                            if result.content
                            else "No data"
                        )
                    except Exception as e:
                        logger.warning(f"MCP tool call {fc.name} failed: {e}")
                        output_text = f"Error calling {fc.name}: {e}"

                    tool_results.append({
                        "type": "function_call_output",
                        "call_id": fc.call_id,
                        "output": output_text,
                    })
                    tool_call_count += 1

                # Continue conversation with tool results -- history is server-side
                response = await client.responses.create(
                    model="gpt-4o-mini",
                    previous_response_id=response.id,
                    input=tool_results,
                    tools=openai_tools,
                )
                iterations += 1

            # Extract final text from response output
            briefing = "\n".join(
                item.text for item in response.output
                if hasattr(item, "text") and item.type == "output_text"
            )

            logger.info(
                f"Briefing generated: {len(briefing)} chars, "
                f"{tool_call_count} tool calls, {iterations} iterations"
            )

            if not briefing:
                return "Habit data was fetched but no briefing was produced. Proceed with a general check-in."

            return briefing
