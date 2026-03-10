# LiveKit Agents + OpenAI Realtime API — Research

**Project:** Accountability Buddy (voice AI, outbound phone calls via Twilio/SIP)
**Researched:** 2026-03-10
**Overall confidence:** HIGH — primary sources are official LiveKit docs and official example repos

---

## 1. How LiveKit Agents Works: Architecture and Lifecycle

### Two-Tier Dispatch Architecture

LiveKit Agents is a Python (or Node.js) framework where your code joins a LiveKit room as a "programmatic participant." The system has two layers:

1. **Agent Server (Worker)**: Registers with LiveKit Cloud and waits for dispatch requests. Runs as a long-lived process.
2. **Job Subprocess**: When dispatched, the agent server spawns an isolated child process per job. One crash cannot affect other sessions.

```
LiveKit Cloud ──dispatch request──> Agent Server ──spawn──> Job Subprocess
                                                              └─ joins room
                                                              └─ runs your entrypoint fn
```

### Entrypoint Function

The entrypoint is the equivalent of a request handler. It's an `async` function registered via the `@server.rtc_session()` decorator. It runs once per job.

```python
from livekit.agents import AgentServer, AgentSession, Agent, JobContext
from livekit.plugins import openai, noise_cancellation
import livekit.agents as agents

server = AgentServer()

@server.rtc_session(agent_name="accountability-buddy")
async def my_agent(ctx: JobContext):
    # ctx.room is the LiveKit room
    # ctx.job.metadata is JSON passed at dispatch time
    session = AgentSession(
        llm=openai.realtime.RealtimeModel(voice="shimmer"),
    )
    await session.start(
        room=ctx.room,
        agent=MyAgent(),
    )

if __name__ == "__main__":
    agents.cli.run_app(server)
```

The older pattern uses `WorkerOptions(entrypoint_fnc=...)` + `cli.run_app(WorkerOptions(...))`. Both patterns are current as of Agents 1.0 (released April 2025).

### Job Metadata

Pass arbitrary JSON at dispatch time. Retrieve in entrypoint:

```python
import json

@server.rtc_session(agent_name="accountability-buddy")
async def my_agent(ctx: JobContext):
    metadata = json.loads(ctx.job.metadata)
    phone_number = metadata["phone_number"]
    user_name = metadata["user_name"]
```

### Session Lifecycle States

`AgentSession` progresses through four states:

1. **Initializing** — setup, no media I/O
2. **Starting** — I/O connections established, agent enters "listening"
3. **Running** — agent cycles between listening / thinking / speaking
4. **Closing** — drains pending speech, closes connections

### Session Events

| Event | Trigger |
|---|---|
| `agent_state_changed` | Agent transitions between states |
| `user_state_changed` | User speaking/listening state changes |
| `user_input_transcribed` | Speech converted to text |
| `conversation_item_added` | Message added to conversation history |
| `close` | Session terminated |

### Shutdown

```python
session.shutdown(drain=True)   # graceful, waits for pending speech
await session.aclose()          # immediate
```

Register cleanup callbacks:

```python
ctx.add_shutdown_callback(my_cleanup_fn)
```

---

## 2. OpenAI Realtime API Integration

LiveKit Agents bridges WebRTC (frontend) to OpenAI Realtime API (WebSocket). The framework handles audio buffer conversion, interruption detection, and transcript synchronization automatically.

### Installation

```bash
uv add "livekit-agents[openai]~=1.4"
# also install noise cancellation for telephony
uv add livekit-plugins-noise-cancellation
```

### Minimal Session Setup with Realtime

```python
from livekit.plugins import openai

session = AgentSession(
    llm=openai.realtime.RealtimeModel(voice="shimmer"),
)
```

No separate STT or TTS is needed — the Realtime API handles speech-in and speech-out end-to-end.

### RealtimeModel Configuration Parameters

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `model` | string | `'gpt-realtime'` | Model version |
| `voice` | string | `'alloy'` | Available: alloy, shimmer, echo, coral, marin, etc. |
| `temperature` | float | 0.8 | Range: 0.6–1.2 |
| `modalities` | list | `['text', 'audio']` | Can set to `['text']` for text-only |
| `turn_detection` | TurnDetection | semantic_vad | See below |

### Turn Detection Modes

**Semantic VAD (default):** Detects turn end using meaning, not silence. Less prone to false interrupts.

```python
from livekit.plugins.openai.realtime import TurnDetection

openai.realtime.RealtimeModel(
    turn_detection=TurnDetection(
        type="semantic_vad",
        eagerness="medium",     # auto | low | medium | high
        create_response=True,
        interrupt_response=True,
    )
)
```

**Server VAD (silence-based):**

```python
TurnDetection(
    type="server_vad",
    threshold=0.5,
    prefix_padding_ms=300,
    silence_duration_ms=500,
)
```

### Text-Only Mode with Custom TTS

If you need fine-grained voice control (e.g., a specific Cartesia voice), use Realtime in text mode + separate TTS:

```python
session = AgentSession(
    llm=openai.realtime.RealtimeModel(modalities=["text"]),
    tts="cartesia/sonic-3",
)
```

### Key Differences from Standard OpenAI (Chat Completions)

| Aspect | Standard OpenAI | OpenAI Realtime |
|---|---|---|
| Pipeline | STT → LLM → TTS (3 hops) | Direct speech-to-speech (1 hop) |
| Latency | Higher (serial pipeline) | Lower |
| VAD | Managed by LiveKit | Built into the model |
| Voice | From TTS provider | From Realtime (alloy, shimmer, etc.) |
| Config | Separate plugin each | Single `RealtimeModel` object |

---

## 3. Giving the Agent Tools / Function Calling

### Defining Tools via Decorator

Tools are methods on the `Agent` subclass decorated with `@function_tool`. The docstring becomes the tool description sent to the LLM.

```python
from livekit.agents import Agent, function_tool, RunContext
from typing import Any

class AccountabilityAgent(Agent):
    def __init__(self, user_name: str):
        super().__init__(
            instructions=SYSTEM_PROMPT,
        )
        self.user_name = user_name

    @function_tool()
    async def log_habit_completion(
        self,
        context: RunContext,
        habit_name: str,
        completed: bool,
        notes: str = "",
    ) -> dict[str, Any]:
        """Record whether the user completed a habit.

        Args:
            habit_name: The name of the habit to log.
            completed: True if completed, False if missed.
            notes: Any additional notes from the user.
        """
        # Call your backend here
        result = await save_to_database(self.user_name, habit_name, completed, notes)
        return {"status": "logged", "habit": habit_name}

    @function_tool()
    async def end_call(self, context: RunContext) -> None:
        """End the call when the conversation is complete."""
        await context.wait_for_playout()
        ctx = get_job_context()
        await ctx.api.room.delete_room(api.DeleteRoomRequest(room=ctx.room.name))
```

### Key Tool Rules

- The `context: RunContext` argument is always first after `self`. It's injected by the framework.
- Return value is auto-converted to string and sent to the LLM as the tool result.
- Return `None` for silent tool completion (no LLM follow-up response generated).
- **Cannot** `await session.generate_reply()` directly inside a tool. Use `await context.wait_for_playout()` instead to sequence speech.
- Interruptions are enabled by default. Disable with `context.disallow_interruptions()`.

### Dynamic Tool Updates

```python
agent.update_tools([new_tool_1, new_tool_2])
```

### Tools at Session Level

Tools can also be shared across all agents in a session:

```python
session = AgentSession(
    llm=openai.realtime.RealtimeModel(voice="shimmer"),
    tools=[global_tool_1, global_tool_2],
    max_tool_steps=5,  # max consecutive tool calls per turn (default: 3)
)
```

---

## 4. Voice and Personality Configuration

### System Prompt / Instructions

The `Agent` class accepts an `instructions` string — this becomes the system prompt:

```python
SYSTEM_PROMPT = """
You are Alex, a warm and encouraging accountability coach.
You are calling {user_name} to check in on their daily habits.

Your style:
- Conversational and supportive, never judgmental
- Keep responses concise — this is a phone call, not an essay
- Celebrate wins, ask curious questions about misses
- Avoid bullet points, markdown, or any formatting
- End the call naturally when the check-in is complete

Today's check-in: ask about sleep, exercise, and water intake.
"""

class AccountabilityAgent(Agent):
    def __init__(self, user_name: str):
        super().__init__(
            instructions=SYSTEM_PROMPT.format(user_name=user_name),
        )
```

### Voice Selection (OpenAI Realtime)

```python
openai.realtime.RealtimeModel(voice="shimmer")
```

Available voices as of 2025: `alloy`, `echo`, `shimmer`, `coral`, `marin`, `verse`, `ash`, `ballad`. Check OpenAI docs for current list.

### Generate an Opening Message

Call `session.generate_reply()` after starting to have the agent speak first (needed for outbound calls where the agent should initiate):

```python
async def on_enter(self) -> None:
    await self.session.generate_reply()
```

Or via the `Agent.on_enter` lifecycle hook (called when an agent becomes active in a session).

### TTS Text Transforms (for pipeline mode)

The session applies these by default when using a pipeline (not Realtime):

```python
session = AgentSession(
    tts_text_transforms=["filter_markdown", "filter_emoji"],
)
```

With Realtime API, these transforms do not apply since the model outputs audio directly.

### Noise Cancellation for Telephony

```python
from livekit.plugins import noise_cancellation
from livekit.agents import RoomInputOptions

await session.start(
    room=ctx.room,
    agent=MyAgent(),
    room_input_options=RoomInputOptions(
        noise_cancellation=noise_cancellation.BVCTelephony(),
    ),
)
```

Use `BVCTelephony()` specifically for phone calls (optimized for PSTN audio characteristics).

---

## 5. Recommended Project Structure

Based on the official `agent-starter-python` template and the outbound-caller-python example:

```
accountability-buddy/
├── .env                        # LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, OPENAI_API_KEY
├── pyproject.toml              # dependencies
├── livekit.toml                # LiveKit Cloud config (agent name, etc.)
└── src/
    ├── __init__.py
    ├── agent.py                # AgentServer, entrypoint, Agent class
    ├── tools.py                # function tools (hangup, DB logging, etc.)
    ├── prompts.py              # system prompts
    ├── scheduler.py            # outbound call dispatch logic (cron/scheduler)
    └── database.py             # habit tracking data access
```

### pyproject.toml

```toml
[project]
name = "accountability-buddy"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "python-dotenv>=1.0",
    "livekit-agents[openai,silero]~=1.4",
    "livekit-plugins-noise-cancellation",
    "livekit>=0.17",            # for server-side API calls (dispatch, SIP)
]
```

### Running the Agent

```bash
# Development (auto-reloads, connects to LiveKit Cloud)
uv run src/agent.py dev

# Production
uv run src/agent.py start
```

---

## 6. Programmatic Dispatch: Triggering Outbound Calls

This is the most critical integration point for this project. The flow is:

1. **Dispatch the agent** — creates a room, assigns the agent to it
2. **Agent connects** to the room
3. **Agent creates a SIP participant** — this triggers the actual phone call via your SIP trunk (Twilio)
4. **Conversation happens** — agent speaks first
5. **Agent ends call** — by deleting the room

### Step 1: Agent Entrypoint (Outbound Pattern)

```python
import json
from livekit import api
from livekit.agents import AgentServer, AgentSession, JobContext, get_job_context
from livekit.plugins import openai, noise_cancellation
import livekit.agents as agents

server = AgentServer()

@server.rtc_session(agent_name="accountability-buddy")
async def entrypoint(ctx: JobContext):
    # Parse metadata from dispatch
    metadata = json.loads(ctx.job.metadata)
    phone_number = metadata["phone_number"]
    user_name = metadata["user_name"]
    user_id = metadata["user_id"]

    # Set up the session with OpenAI Realtime
    session = AgentSession(
        llm=openai.realtime.RealtimeModel(voice="shimmer"),
    )

    # Start the session (agent connects to room)
    await session.start(
        room=ctx.room,
        agent=AccountabilityAgent(user_name=user_name, user_id=user_id),
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVCTelephony(),
        ),
    )

    # Now dial the user via SIP
    try:
        await ctx.api.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                room_name=ctx.room.name,
                sip_trunk_id=SIP_TRUNK_ID,    # from env var
                sip_call_to=phone_number,
                participant_identity=phone_number,
                participant_name=user_name,
                wait_until_answered=True,      # block until answered
                krisp_enabled=True,
            )
        )
    except Exception as e:
        # Handle unanswered / busy / failed calls
        logger.error(f"SIP call failed: {e}")
        await session.aclose()
        return

if __name__ == "__main__":
    agents.cli.run_app(server)
```

### Step 2: Triggering a Dispatch from Your Scheduler

```python
import asyncio
import json
from livekit import api

async def trigger_accountability_call(user_id: str, user_name: str, phone_number: str):
    """Call this from your cron job / scheduler."""
    async with api.LiveKitAPI() as lkapi:
        dispatch = await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name="accountability-buddy",
                room="",                        # empty string = create new room
                metadata=json.dumps({
                    "user_id": user_id,
                    "user_name": user_name,
                    "phone_number": phone_number,
                })
            )
        )
        return dispatch

# Example: trigger from anywhere in your Python codebase
asyncio.run(trigger_accountability_call(
    user_id="usr_123",
    user_name="Sarah",
    phone_number="+15105550123",
))
```

### Alternative: CLI Dispatch (for testing)

```bash
lk dispatch create \
  --new-room \
  --agent-name accountability-buddy \
  --metadata '{"phone_number": "+15105550123", "user_name": "Sarah", "user_id": "usr_123"}'
```

### Hanging Up (end_call tool)

```python
from livekit import api
from livekit.agents import get_job_context

async def hangup_call():
    ctx = get_job_context()
    if ctx and ctx.room:
        await ctx.api.room.delete_room(
            api.DeleteRoomRequest(room=ctx.room.name)
        )
```

### Important: agent_name Disables Auto-Dispatch

When you set `agent_name` on `@server.rtc_session`, LiveKit **disables automatic dispatch**. The agent will only run when explicitly dispatched. This is what you want for outbound calling.

---

## 7. SIP / Twilio Setup Requirements

For outbound calls via Twilio:

1. **Purchase a phone number** from Twilio (or use existing number)
2. **Configure Twilio as a SIP trunk** in LiveKit Cloud:
   - Create an outbound SIP trunk in LiveKit pointing to Twilio's SIP termination URI
   - Configure credentials
3. **Get the trunk ID** — looks like `ST_xxxxxxxxxx`
4. **Set env var**: `SIP_OUTBOUND_TRUNK_ID=ST_xxxxxxxxxx`

LiveKit SIP supports: SIP over UDP/TCP/TLS, DTMF, caller ID, SRTP encryption. It does **not** support video over SIP or SIP registration.

For inbound calls (if needed later): create an inbound trunk + dispatch rule pointing to the same `agent_name`.

---

## 8. Complete Minimal Working Example

This synthesizes all the above into a single file for the accountability buddy pattern:

```python
# src/agent.py
import json
import logging
import os
from typing import Any

from dotenv import load_dotenv
from livekit import api
from livekit.agents import (
    Agent, AgentServer, AgentSession, JobContext, RunContext,
    RoomInputOptions, function_tool, get_job_context,
)
from livekit.plugins import openai, noise_cancellation
import livekit.agents as agents

load_dotenv()
logger = logging.getLogger(__name__)

SIP_TRUNK_ID = os.environ["SIP_OUTBOUND_TRUNK_ID"]

SYSTEM_PROMPT = """
You are Alex, a warm accountability coach calling {user_name}.
Check in on their daily habits: sleep, exercise, water intake.
Be conversational and brief — this is a phone call.
Never use bullet points or markdown formatting.
Use the log_habit tool after discussing each habit.
Use the end_call tool when the check-in is complete.
"""

class AccountabilityAgent(Agent):
    def __init__(self, user_name: str, user_id: str):
        super().__init__(
            instructions=SYSTEM_PROMPT.format(user_name=user_name),
        )
        self.user_name = user_name
        self.user_id = user_id

    async def on_enter(self) -> None:
        # Agent speaks first on outbound calls
        await self.session.generate_reply()

    @function_tool()
    async def log_habit(
        self,
        context: RunContext,
        habit: str,
        completed: bool,
        notes: str = "",
    ) -> dict[str, Any]:
        """Log whether the user completed a habit today.

        Args:
            habit: Name of the habit (sleep, exercise, water, etc.)
            completed: Whether they completed it.
            notes: Any details the user mentioned.
        """
        logger.info(f"Logging habit: {habit}={completed} for {self.user_id}")
        # TODO: call your database/API here
        return {"logged": True, "habit": habit, "completed": completed}

    @function_tool()
    async def end_call(self, context: RunContext) -> None:
        """End the call after the check-in is complete."""
        await context.wait_for_playout()
        ctx = get_job_context()
        if ctx:
            await ctx.api.room.delete_room(
                api.DeleteRoomRequest(room=ctx.room.name)
            )


server = AgentServer()

@server.rtc_session(agent_name="accountability-buddy")
async def entrypoint(ctx: JobContext):
    metadata = json.loads(ctx.job.metadata)
    phone_number = metadata["phone_number"]
    user_name = metadata["user_name"]
    user_id = metadata["user_id"]

    session = AgentSession(
        llm=openai.realtime.RealtimeModel(voice="shimmer"),
    )

    await session.start(
        room=ctx.room,
        agent=AccountabilityAgent(user_name=user_name, user_id=user_id),
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVCTelephony(),
        ),
    )

    try:
        await ctx.api.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                room_name=ctx.room.name,
                sip_trunk_id=SIP_TRUNK_ID,
                sip_call_to=phone_number,
                participant_identity=phone_number,
                participant_name=user_name,
                wait_until_answered=True,
                krisp_enabled=True,
            )
        )
    except Exception as e:
        logger.error(f"Outbound call failed for {user_name} ({phone_number}): {e}")
        await session.aclose()


if __name__ == "__main__":
    agents.cli.run_app(server)
```

---

## 9. Key Pitfalls and Gotchas

### Setting agent_name Disables Auto-Dispatch
When `agent_name` is set, the agent **only runs via explicit dispatch**. You cannot test it by just running `dev` mode and connecting a browser — you must dispatch via CLI or API.

### Agent Must Speak First (Outbound)
For outbound calls, the caller expects the agent to speak first. Implement `on_enter` to call `generate_reply()`. Without this, the call connects in silence.

### wait_until_answered is Required for Ordering
Without `wait_until_answered=True` in `CreateSIPParticipantRequest`, the session may try to start before the call is answered, causing the agent to speak to a dial tone.

### Tool Speech Coordination
Do not `await session.generate_reply()` inside a tool. Use `await context.wait_for_playout()` to sequence speech within tool calls. Failure to do this causes race conditions in audio output.

### BVCTelephony vs BVC
Use `noise_cancellation.BVCTelephony()` for phone calls (PSTN audio), not `noise_cancellation.BVC()` which is optimized for microphone input.

### Realtime API Modalities for History
Loading conversation history into the Realtime API may require `modalities=["text"]` to avoid unexpected text-only responses when using historical context.

### Max Tool Steps
Default `max_tool_steps=3`. If your agent needs to log multiple habits in sequence (sleep, exercise, water = 3 tools minimum), this default is fine. Increase if adding more.

### Session Cleanup
Always delete the room to hang up (not just close the session). `delete_room` disconnects all SIP participants. If you only close the session, the phone line may stay open.

---

## 10. Sources

- [LiveKit Agents Introduction](https://docs.livekit.io/agents/) — HIGH confidence, official docs
- [OpenAI Realtime Plugin Guide](https://docs.livekit.io/agents/models/realtime/plugins/openai/) — HIGH confidence, official docs
- [Voice AI Quickstart](https://docs.livekit.io/agents/start/voice-ai-quickstart/) — HIGH confidence, official docs
- [Agent Session Docs](https://docs.livekit.io/agents/logic/sessions/) — HIGH confidence, official docs
- [Job Lifecycle Docs](https://docs.livekit.io/agents/server/job/) — HIGH confidence, official docs
- [Tool Definition and Use](https://docs.livekit.io/agents/logic/tools/) — HIGH confidence, official docs
- [Agent Dispatch Docs](https://docs.livekit.io/agents/build/dispatch/) — HIGH confidence, official docs
- [Outbound Calls Quickstart](https://docs.livekit.io/agents/quickstarts/outbound-calls/) — HIGH confidence, official docs
- [Outbound SIP Calling](https://docs.livekit.io/telephony/making-calls/outbound-calls/) — HIGH confidence, official docs
- [outbound-caller-python example](https://github.com/livekit-examples/outbound-caller-python) — HIGH confidence, official LiveKit example repo
- [agent-starter-python example](https://github.com/livekit-examples/agent-starter-python) — HIGH confidence, official LiveKit example repo
- [tabtablabs: LiveKit + OpenAI Realtime tutorial](https://tabtablabs.com/blog/livekit-openai-realtime-voice-agent) — MEDIUM confidence, third-party walkthrough with complete code
