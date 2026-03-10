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
from livekit.agents.llm import mcp
from livekit.plugins import openai, noise_cancellation

from habitify_auth import refresh_habitify_token
from habitify_briefing import generate_briefing

load_dotenv(dotenv_path=".env.local")

logger = logging.getLogger("accountability-buddy")
logger.setLevel(logging.INFO)

outbound_trunk_id = os.getenv("SIP_OUTBOUND_TRUNK_ID")
default_phone = os.getenv("DEFAULT_PHONE_NUMBER")

SYSTEM_PROMPT = """\
You are an accountability coach calling for an evening habit check-in. \
Your tone is direct, firm, and results-focused. You do not hedge, apologize, \
or soften your assessments.

Completed habits get brief acknowledgment. Incomplete habits get challenged: \
why didn't you do it, and what's the plan to fix it.

You're calling to check in on the user's day and hold them accountable. \
Reference the habit briefing to discuss specific habits, patterns, and streaks.

When the user confirms completing a habit, use the complete-habit tool with the habit ID \
and today's date from the briefing to record it. The briefing includes habit IDs as UUIDs \
in parentheses like "(id: FE112B42-...)" and the date in YYYY-MM-DD format. \
Both habitId and date are required parameters for complete-habit. \
For goal-based habits with numeric targets, ask for the value and use add-habit-log \
with the habitId and value. Do not log habits the user says they skipped or didn't do.

Rules:
- This is a voice call. Never use bullet points, markdown, or numbered lists.
- Keep responses concise — under 3 sentences unless following up on a specific habit.
- When the check-in is complete, say a direct goodbye and use the end_call tool.
- If you hear a voicemail greeting or automated system instead of a real person, \
immediately call the detected_answering_machine tool. Do not leave a message.\
"""


class AccountabilityAgent(Agent):
    def __init__(self, briefing: str = "") -> None:
        end_call_tool = EndCallTool(
            delete_room=True,
            end_instructions="Say a direct, firm goodbye. No fluff.",
        )
        instructions = SYSTEM_PROMPT
        if briefing:
            instructions += f"\n\n## Today's Habit Briefing\n\n{briefing}"
            instructions += "\n\nUse this briefing to guide the conversation. Reference specific patterns and streaks."
            instructions += "\nWhen the user confirms a habit is done, use the complete-habit tool with the habit ID from the briefing to record it."
            instructions += "\nFor goal-based habits (with numeric targets), ask for the specific number and use add-habit-log with the habit ID."
            instructions += "\nDo NOT mark habits the user says they skipped — leave them as-is."
        super().__init__(
            instructions=instructions,
            tools=end_call_tool.tools,
        )

    async def on_enter(self) -> None:
        """Agent speaks first — initiate the accountability check-in immediately."""
        await self.session.generate_reply(
            instructions="Greet the user and kick off the accountability check-in. Reference specific habits from today's briefing. Be direct — no small talk."
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
    for item in session.history:
        entry = {"role": item.role, "type": item.type}
        if hasattr(item, "text") and item.text:
            entry["text"] = item.text
        if hasattr(item, "tool_calls") and item.tool_calls:
            entry["tool_calls"] = [
                {"name": tc.name, "arguments": tc.arguments}
                for tc in item.tool_calls
            ]
        if hasattr(item, "tool_call_id") and item.tool_call_id:
            entry["tool_call_id"] = item.tool_call_id
            if hasattr(item, "content") and item.content:
                entry["content"] = item.content
        history.append(entry)

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

    # Stage 2: Voice agent with MCP write tools for real-time habit logging
    habitify_mcp = None
    if habitify_token:
        habitify_mcp = mcp.MCPServerHTTP(
            url="https://mcp.habitify.me/mcp",
            transport_type="sse",
            headers={"Authorization": f"Bearer {habitify_token}"},
        )

    session = AgentSession(
        llm=openai.realtime.RealtimeModel(voice="shimmer"),
        mcp_servers=[habitify_mcp] if habitify_mcp else [],
        max_tool_steps=10,
    )

    # Log conversation when session ends
    @session.on("close")
    async def _on_session_close(*args):
        await _save_conversation_log(session, ctx.room.name)

    # 1. Start session in background -- agent ready before call connects
    session_started = asyncio.create_task(
        session.start(
            agent=AccountabilityAgent(briefing=briefing),
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
