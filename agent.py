from __future__ import annotations

import asyncio
import json
import logging
import os

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
Ask what they accomplished today and challenge them on anything they avoided.

Rules:
- This is a voice call. Never use bullet points, markdown, or numbered lists.
- Keep responses concise — under 3 sentences unless following up on a specific habit.
- When the check-in is complete, say a direct goodbye and use the end_call tool.
- If you hear a voicemail greeting or automated system instead of a real person, \
immediately call the detected_answering_machine tool. Do not leave a message.\
"""


class AccountabilityAgent(Agent):
    def __init__(self) -> None:
        end_call_tool = EndCallTool(
            delete_room=True,
            end_instructions="Say a direct, firm goodbye. No fluff.",
        )
        super().__init__(
            instructions=SYSTEM_PROMPT,
            tools=end_call_tool.tools,
        )

    async def on_enter(self) -> None:
        """Agent speaks first — initiate the accountability check-in immediately."""
        await self.session.generate_reply(
            instructions="Greet the user and kick off the accountability check-in. Be direct — no small talk."
        )

    @function_tool()
    async def detected_answering_machine(self, ctx: RunContext):
        """Called when the call reaches voicemail. Use this tool AFTER you hear the voicemail greeting"""
        logger.info("Voicemail detected -- hanging up without leaving a message")
        job_ctx = get_job_context()
        await job_ctx.api.room.delete_room(
            api.DeleteRoomRequest(room=job_ctx.room.name)
        )


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    # Parse phone number from dispatch metadata, fall back to env var
    metadata = json.loads(ctx.job.metadata or "{}")
    phone_number = metadata.get("phone") or metadata.get("phone_number") or default_phone
    if not phone_number:
        logger.error("No phone number provided in metadata or DEFAULT_PHONE_NUMBER env var")
        ctx.shutdown()
        return

    session = AgentSession(
        llm=openai.realtime.RealtimeModel(voice="shimmer"),
    )

    # 1. Start session in background -- agent ready before call connects
    session_started = asyncio.create_task(
        session.start(
            agent=AccountabilityAgent(),
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
