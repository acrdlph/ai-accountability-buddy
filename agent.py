from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv
from livekit import api
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    RunContext,
    RoomInputOptions,
    function_tool,
    get_job_context,
    cli,
    WorkerOptions,
)
from livekit.plugins import openai, noise_cancellation

load_dotenv(dotenv_path=".env.local")

logger = logging.getLogger("accountability-buddy")
logger.setLevel(logging.INFO)

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
- When the check-in is complete, say a direct goodbye and use the end_call tool.\
"""


class AccountabilityAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=SYSTEM_PROMPT)

    async def on_enter(self) -> None:
        """Agent speaks first — initiate the accountability check-in immediately."""
        await self.session.generate_reply(
            instructions="Greet the user and kick off the accountability check-in. Be direct — no small talk."
        )

    @function_tool()
    async def end_call(self, ctx: RunContext) -> None:
        """End the call when the habit check-in conversation is complete."""
        current_speech = ctx.session.current_speech
        if current_speech:
            await current_speech.wait_for_playout()

        job_ctx = get_job_context()
        await job_ctx.api.room.delete_room(
            api.DeleteRoomRequest(room=job_ctx.room.name)
        )


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    session = AgentSession(
        llm=openai.realtime.RealtimeModel(voice="shimmer"),
    )

    # Start session in background — preserves Phase 2 SIP insertion point
    session_started = asyncio.create_task(
        session.start(
            agent=AccountabilityAgent(),
            room=ctx.room,
            room_input_options=RoomInputOptions(
                noise_cancellation=noise_cancellation.BVCTelephony(),
            ),
        )
    )

    # Phase 2: SIP participant creation (create_sip_participant) goes here

    await session_started


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="accountability-buddy",
        )
    )
