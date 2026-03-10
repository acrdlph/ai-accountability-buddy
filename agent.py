from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from livekit import api
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    RoomInputOptions,
    RunContext,
    cli,
    WorkerOptions,
    function_tool,
    get_job_context,
)
from livekit.agents.beta.tools import EndCallTool
from livekit.plugins import openai, noise_cancellation
from mcp import ClientSession
from mcp.client.sse import sse_client

from habitify_auth import refresh_habitify_token
from habitify_briefing import generate_briefing

load_dotenv(dotenv_path=".env.local")

logger = logging.getLogger("accountability-buddy")
logger.setLevel(logging.INFO)

outbound_trunk_id = os.getenv("SIP_OUTBOUND_TRUNK_ID")
default_phone = os.getenv("DEFAULT_PHONE_NUMBER")

SYSTEM_PROMPT_BEFORE_BRIEFING = """\
You are an accountability coach calling for an evening habit check-in. \
Tone: direct, firm, results-focused.\
"""

SYSTEM_PROMPT_AFTER_BRIEFING = """\
Go through each habit above. Completed habits get brief acknowledgment. \
Incomplete habits get challenged: why not, and what's the plan.

IMMEDIATELY log every habit the user confirms — do not wait or batch them. \
The moment the user says they did something, call complete_habit right then. \
All habits are simple completions — always use complete_habit, never add_habit_log. \
Do NOT log habits the user says they skipped.

Rules: This is a voice call. No markdown, no lists. Keep responses under 3 sentences. \
When done, say goodbye and call end_call. \
If you hear a voicemail greeting, call detected_answering_machine immediately.\
"""


HABITIFY_MCP_URL = "https://mcp.habitify.me/mcp"


async def _call_habitify_tool(token: str, tool_name: str, arguments: dict) -> str:
    """Open a fresh MCP connection, call a tool, return the result text.

    The Habitify MCP SSE connection drops after ~30s of inactivity, so we
    cannot keep a long-lived connection open during a voice call. Instead,
    each tool call opens its own short-lived connection.
    """
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with sse_client(HABITIFY_MCP_URL, headers=headers) as (rs, ws):
            async with ClientSession(rs, ws) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                text = result.content[0].text if result.content else "Done"
                logger.info(f"Habitify {tool_name} succeeded: {text[:100]}")
                return text
    except Exception as e:
        logger.error(f"Habitify {tool_name} failed: {e}")
        return f"Error: {e}"


class AccountabilityAgent(Agent):
    def __init__(self, briefing: str = "", habitify_token: str = "") -> None:
        self._habitify_token = habitify_token
        end_call_tool = EndCallTool(
            delete_room=True,
            end_instructions="Say a direct, firm goodbye. No fluff.",
        )
        if briefing:
            instructions = (
                SYSTEM_PROMPT_BEFORE_BRIEFING
                + f"\n\n{briefing}\n\n"
                + SYSTEM_PROMPT_AFTER_BRIEFING
            )
        else:
            instructions = (
                SYSTEM_PROMPT_BEFORE_BRIEFING
                + "\n\nNo habit data available. Do a general accountability check-in.\n\n"
                + SYSTEM_PROMPT_AFTER_BRIEFING
            )
        super().__init__(
            instructions=instructions,
            tools=end_call_tool.tools,
        )

    async def on_enter(self) -> None:
        """Agent speaks first — initiate the accountability check-in immediately."""
        await self.session.generate_reply(
            instructions="Greet the user and kick off the accountability check-in. ONLY mention habits that appear in the HABIT ID REFERENCE section of the briefing. Never invent habits. Be direct — no small talk."
        )

    @function_tool()
    async def complete_habit(self, ctx: RunContext, habitId: str, date: str):
        """Mark a habit as completed. Use when the user confirms they did a habit. Requires habitId (UUID from briefing) and date (YYYY-MM-DD)."""
        logger.info(f"Completing habit {habitId} for {date}")
        return await _call_habitify_tool(
            self._habitify_token, "complete-habit", {"habitId": habitId, "date": date}
        )

    @function_tool()
    async def detected_answering_machine(self, ctx: RunContext):
        """Called when the call reaches voicemail. Use this tool AFTER you hear the voicemail greeting"""
        logger.info("Voicemail detected -- hanging up without leaving a message")
        job_ctx = get_job_context()
        await job_ctx.api.room.delete_room(
            api.DeleteRoomRequest(room=job_ctx.room.name)
        )


async def _save_conversation_log(session: AgentSession, room_name: str) -> None:
    """Save conversation history to a log file after the session ends."""
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = log_dir / f"conversation_{room_name}_{timestamp}.json"

    history = []
    for item in session.history.items:
        if item.type == "message":
            history.append({
                "role": item.role,
                "text": item.text_content,
            })
        elif item.type == "function_call":
            history.append({
                "role": "assistant",
                "tool_call": item.name,
                "arguments": item.raw_arguments if hasattr(item, "raw_arguments") else str(item.arguments),
            })
        elif item.type == "function_call_output":
            history.append({
                "role": "tool",
                "output": item.text_content if hasattr(item, "text_content") else str(item),
            })

    filepath.write_text(json.dumps(history, indent=2, default=str))
    logger.info(f"Conversation log saved to {filepath}")


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    # Parse phone number from dispatch metadata, fall back to env var
    metadata = json.loads(ctx.job.metadata or "{}")
    phone_number = metadata.get("phone") or metadata.get("phone_number") or default_phone
    if not phone_number:
        logger.error("No phone number provided in metadata or DEFAULT_PHONE_NUMBER env var")
        ctx.shutdown()
        return

    # Stage 1: Pre-call reasoning -- fetch and analyze habits via MCP
    briefing = ""
    habitify_token = None
    try:
        habitify_token = await refresh_habitify_token()
        briefing = await generate_briefing(habitify_token)
        logger.info(f"Pre-call briefing generated ({len(briefing)} chars)")
    except Exception as e:
        logger.error(f"Pre-call briefing failed: {e}")
        briefing = "Could not fetch habit data. Do a general accountability check-in."

    # Stage 2: Voice agent with custom Habitify tools (fresh MCP connection per call)
    session = AgentSession(
        llm=openai.realtime.RealtimeModel(voice="shimmer"),
        max_tool_steps=10,
    )

    # Log conversation when session ends
    @session.on("close")
    def _on_session_close(*args):
        asyncio.create_task(_save_conversation_log(session, ctx.room.name))

    # 1. Start session in background -- agent ready before call connects
    session_started = asyncio.create_task(
        session.start(
            agent=AccountabilityAgent(briefing=briefing, habitify_token=habitify_token or ""),
            room=ctx.room,
            room_input_options=RoomInputOptions(
                noise_cancellation=noise_cancellation.BVCTelephony(),
            ),
        )
    )

    # 2. Dial the phone -- blocks until answered or fails
    try:
        await ctx.api.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                room_name=ctx.room.name,
                sip_trunk_id=outbound_trunk_id,
                sip_call_to=phone_number,
                participant_identity=phone_number,
                wait_until_answered=True,
            )
        )
    except api.TwirpError as e:
        logger.error(
            f"SIP call failed: {e.message}, "
            f"SIP status: {e.metadata.get('sip_status_code')} "
            f"{e.metadata.get('sip_status')}"
        )
        ctx.shutdown()
        return

    # 3. Wait for session and participant
    await session_started
    participant = await ctx.wait_for_participant(identity=phone_number)
    logger.info(f"Participant joined: {participant.identity}")


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="accountability-buddy",
        )
    )
