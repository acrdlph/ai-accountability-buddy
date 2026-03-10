# Phase 1: Core Voice Agent — Research

**Researched:** 2026-03-10
**Domain:** LiveKit Agents 1.x + OpenAI Realtime API, Python voice agent bootstrap
**Confidence:** HIGH

---

## Summary

Phase 1 produces a working LiveKit voice agent with the tough-love accountability personality, verifiable entirely in the browser without any phone calls. The official `outbound-caller-python` template is the correct bootstrap — it implements the exact same stack this project needs and removes all boilerplate. The primary job of this phase is to adapt that template: swap the dental demo persona for the accountability personality, replace the STT+LLM+TTS pipeline with `openai.realtime.RealtimeModel`, and add `on_enter` → `generate_reply()` so the agent speaks first.

The most important technical nuance in this phase is the session-start ordering: the official template uses `asyncio.create_task(session.start(...))` to kick off session start in the background, then calls `create_sip_participant()` to dial, then `await`s both. This ordering is critical for outbound calls and should be preserved even in Phase 1 (before Twilio is wired up) so Phase 2 can drop SIP dialing in cleanly. For Phase 1 browser testing, the SIP block is omitted, but the session structure must be correct.

The `end_call` tool pattern — `await context.wait_for_playout()` then `delete_room()` — is already validated in the official template source (with minor naming difference: the template uses `ctx.session.current_speech.wait_for_playout()` directly, not `context.wait_for_playout()`). Room deletion is the correct and only reliable hangup mechanism; closing the session alone leaves connections open.

**Primary recommendation:** Bootstrap from `outbound-caller-python`, replace the LLM pipeline with `openai.realtime.RealtimeModel(voice="shimmer")`, write the tough-love system prompt, implement `on_enter` for agent-speaks-first, and verify in LiveKit browser playground.

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| FR3 | Conversational Check-In — natural voice conversation reviewing each habit | AgentSession + RealtimeModel provides full speech-to-speech; system prompt drives conversation structure |
| FR5 | Tough-Love Personality — direct, no-nonsense, challenges on incomplete habits | System prompt in `Agent(instructions=...)` controls tone; documented pattern for phone-style prompts |
| FR6 | Agent Speaks First — agent initiates within 1-2 seconds of connection | `on_enter()` → `await self.session.generate_reply()` — documented LiveKit pattern; confirmed in research |
| FR9 | Graceful Call Termination — agent says goodbye, room deleted cleanly | `end_call` function tool calling `delete_room()` — exact pattern in official template source |
| NFR1 | Latency — sub-second responses, single-hop speech-to-speech | `openai.realtime.RealtimeModel` eliminates STT+TTS hops; confirmed in LiveKit+OpenAI docs |
| NFR2 | Audio Quality — telephony-optimized noise cancellation | `noise_cancellation.BVCTelephony()` in `RoomInputOptions` — PSTN-specific plugin, confirmed in docs |
</phase_requirements>

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| livekit-agents[openai] | ~=1.4 (stable: 1.4.4) | Agent orchestration framework, session lifecycle, function tools | Official LiveKit framework; 1.x is current stable series |
| livekit-plugins-noise-cancellation | ~=0.2 | BVCTelephony for PSTN audio quality | Required for telephony audio; separate install from agents |
| livekit | >=1.0 | Server-side API calls (room delete, future SIP dispatch) | Python server SDK for LiveKit APIs |
| openai | (auto-installed with agents[openai]) | OpenAI Realtime API via livekit plugin | Realtime plugin bundled with agents[openai] extra |
| python-dotenv | >=1.0 | Load `.env` for local dev | Standard env management |

**Version note:** 1.5.0rc2 is available (March 6, 2026) but is a release candidate. Pin to `~=1.4` for stability. Upgrade to 1.5 once stable.

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| uv | latest | Package manager and virtual env | LiveKit ecosystem standard; replaces pip/venv |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| openai.realtime.RealtimeModel | deepgram.STT + openai.LLM + cartesia.TTS | Pipeline has more latency (3 serial hops); Cartesia offers better voice control but requires text-mode Realtime; use Realtime for lowest latency per NFR1 |
| BVCTelephony | BVC (generic) | BVC is optimized for microphone input, not PSTN audio; BVCTelephony is tuned for telephony characteristics |

**Installation:**

```bash
uv add "livekit-agents[openai]~=1.4"
uv add livekit-plugins-noise-cancellation
uv add "livekit>=1.0"
uv add python-dotenv
# After install, download required model files:
uv run agent.py download-files
```

---

## Architecture Patterns

### Recommended Project Structure

```
accountability-buddy/
├── .env.local              # LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, OPENAI_API_KEY
├── pyproject.toml          # uv-managed dependencies
├── livekit.toml            # LiveKit Cloud agent name config
└── agent.py                # Single file for Phase 1: AgentServer, entrypoint, AccountabilityAgent
```

Single-file structure is appropriate for Phase 1. Refactor to `src/` layout in Phase 3+ when adding Habitify client and scheduler.

### Pattern 1: Session Start + Outbound Dial Ordering

**What:** Start the session in a background task, then dial, then await both. This ensures the agent is ready to handle audio the moment the call is answered.

**When to use:** Always — this is the correct ordering for outbound calls. Phase 1 omits the SIP block but uses the same structural pattern for Phase 2 compatibility.

```python
# Source: https://github.com/livekit-examples/outbound-caller-python/blob/main/agent.py (verbatim)

async def entrypoint(ctx: JobContext):
    await ctx.connect()

    dial_info = json.loads(ctx.job.metadata)
    phone_number = dial_info["phone_number"]

    agent = AccountabilityAgent(name="Achill")

    session = AgentSession(
        llm=openai.realtime.RealtimeModel(voice="shimmer"),
    )

    # Start session in background FIRST — agent connects to room
    session_started = asyncio.create_task(
        session.start(
            agent=agent,
            room=ctx.room,
            room_input_options=RoomInputOptions(
                noise_cancellation=noise_cancellation.BVCTelephony(),
            ),
        )
    )

    # Phase 2 will insert create_sip_participant() here

    await session_started
```

**Phase 1 note:** Without SIP, the agent is accessible immediately from the browser playground after `session_started` completes.

### Pattern 2: Agent Speaks First (on_enter)

**What:** Override `on_enter()` in the Agent subclass to call `generate_reply()`. This is invoked when the agent becomes active in a session.

**When to use:** Any outbound scenario where the agent should initiate. Required for FR6.

```python
# Source: confirmed in LiveKit Agents docs + research/livekit-openai.md section 4

class AccountabilityAgent(Agent):
    def __init__(self):
        super().__init__(instructions=SYSTEM_PROMPT)

    async def on_enter(self) -> None:
        # Agent speaks first — no waiting, no silence
        await self.session.generate_reply()
```

**Important:** `generate_reply()` with no arguments uses the system prompt to determine the opening message. This is correct for Phase 1. In Phase 3, pass habit context via instructions or metadata.

### Pattern 3: end_call Tool with Playout Wait

**What:** Function tool that lets the LLM terminate the call. Uses `current_speech.wait_for_playout()` to ensure the goodbye message plays fully before deleting the room.

**When to use:** Always — the only reliable hangup mechanism is room deletion.

```python
# Source: https://github.com/livekit-examples/outbound-caller-python/blob/main/agent.py (verbatim, adapted)

@function_tool()
async def end_call(self, ctx: RunContext) -> None:
    """End the call when the conversation is complete."""
    # Wait for any in-progress speech to finish playing
    current_speech = ctx.session.current_speech
    if current_speech:
        await current_speech.wait_for_playout()

    # Delete the room to disconnect all participants and terminate the session
    job_ctx = get_job_context()
    await job_ctx.api.room.delete_room(
        api.DeleteRoomRequest(room=job_ctx.room.name)
    )
```

**Note on `context.wait_for_playout()`:** The research notes mention this API, but the official template source uses `ctx.session.current_speech.wait_for_playout()` directly. Use the template's pattern; it is verified working.

### Pattern 4: System Prompt for Phone-Only Personality

**What:** System prompt tuned for voice (no markdown), tough-love tone, and concise responses. Phone conversations have different norms than chat.

**When to use:** Always — Realtime API outputs audio directly; markdown in prompt leaks into speech.

```python
# Source: adapted from research/livekit-openai.md section 4

SYSTEM_PROMPT = """
You are an accountability coach calling {name} for their evening habit check-in.
Your tone is direct, firm, and results-focused. You do not hedge, apologize, or soften assessments.
Completed habits get brief acknowledgment. Incomplete habits get challenged: why, and what's the plan.

Rules:
- This is a voice call. Never use bullet points, markdown, or numbered lists.
- Keep responses concise — under 3 sentences unless asking a follow-up.
- When all habits are reviewed, say a direct goodbye and use the end_call tool.
"""
```

### Anti-Patterns to Avoid

- **`await session.start()` before dialing:** Correct for browser-only, but breaks Phase 2 outbound ordering. Use `asyncio.create_task()` from the start.
- **Calling `generate_reply()` inside a function tool body:** Causes race conditions. Use `ctx.session.current_speech.wait_for_playout()` to sequence speech within tools, not `generate_reply()`.
- **Closing session instead of deleting room:** `session.aclose()` or `session.shutdown()` leaves the phone line open. Only `delete_room()` disconnects SIP participants.
- **Using `BVC()` instead of `BVCTelephony()`:** Wrong noise profile for PSTN. Always use `BVCTelephony()` in this project.
- **Setting `agent_name` and expecting auto-dispatch:** When `agent_name` is set in `WorkerOptions`, the agent only runs via explicit dispatch. For browser testing in `dev` mode, dispatch via CLI: `lk dispatch create --new-room --agent-name accountability-buddy`.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Speech-to-speech voice AI | Custom STT+LLM+TTS pipeline | `openai.realtime.RealtimeModel` | Realtime handles VAD, interruptions, and latency in one hop |
| Turn detection | Manual silence detection | Semantic VAD built into RealtimeModel | Semantic VAD detects turn-end by meaning, not silence — fewer false interrupts |
| Noise cancellation | Audio filtering code | `noise_cancellation.BVCTelephony()` | LiveKit's BVC plugin is tuned for PSTN characteristics; not replicable without training data |
| Room teardown / hangup | Custom disconnect logic | `ctx.api.room.delete_room()` | The only mechanism guaranteed to disconnect all SIP participants |
| Agent dispatch | HTTP calls to LiveKit API | `cli.run_app(WorkerOptions(...))` + `lk dispatch create` | Framework handles job isolation, worker registration, reconnection |
| Audio buffer management | Manual WebRTC audio handling | `AgentSession` | Framework converts WebRTC audio to Realtime API format automatically |

**Key insight:** Every component of voice I/O, turn detection, and session lifecycle is handled by the LiveKit Agents framework and OpenAI Realtime. The agent code is entirely business logic: system prompt, tools, and personality.

---

## Common Pitfalls

### Pitfall 1: Agent Name Disables Auto-Dispatch

**What goes wrong:** The agent runs in `dev` mode but never receives any traffic. Browser playground connects but nothing happens.

**Why it happens:** Setting `agent_name` in `WorkerOptions` disables automatic dispatch. LiveKit holds the agent and waits for an explicit dispatch request.

**How to avoid:** After starting `python agent.py dev`, dispatch via CLI in a second terminal:
```bash
lk dispatch create --new-room --agent-name accountability-buddy
```
For browser testing with no metadata, pass minimal JSON: `--metadata '{}'`.

**Warning signs:** Agent log says "connected" but playground shows no agent joining the room.

### Pitfall 2: Agent Speaks Into Silence

**What goes wrong:** Call connects. Agent says nothing. User waits. Awkward silence.

**Why it happens:** `on_enter()` not implemented, or `generate_reply()` not called, or called before `session_started` is awaited.

**How to avoid:** Implement `on_enter` in the Agent subclass. Confirm `await session_started` completes before the agent could speak.

**Warning signs:** Playground shows agent connected but no audio output for the first 5+ seconds.

### Pitfall 3: Goodbye Message Gets Cut Off

**What goes wrong:** Agent says "Goodbye" but gets cut off mid-word because the room is deleted immediately.

**Why it happens:** `delete_room()` is called without waiting for audio playback to finish.

**How to avoid:** In the `end_call` tool, always check and await `ctx.session.current_speech.wait_for_playout()` before deleting the room.

**Warning signs:** Last word of goodbye is truncated; user hears click mid-sentence.

### Pitfall 4: Wrong Noise Cancellation Plugin

**What goes wrong:** Audio quality is fine in browser, degraded when testing with actual telephony in Phase 2.

**Why it happens:** Using `noise_cancellation.BVC()` (microphone-optimized) instead of `noise_cancellation.BVCTelephony()` (PSTN-optimized).

**How to avoid:** Use `BVCTelephony()` from the beginning so Phase 2 test calls have correct audio.

**Warning signs:** Muffled or processed-sounding audio on phone calls in Phase 2.

### Pitfall 5: Download-Files Step Skipped

**What goes wrong:** `python agent.py dev` crashes with a model file not found error.

**Why it happens:** The noise cancellation and turn detection plugins require local model files that are not included in the package install.

**How to avoid:** Run `uv run agent.py download-files` after `uv sync` and before the first `dev` run.

**Warning signs:** `FileNotFoundError` or `ModelNotFoundError` on startup.

### Pitfall 6: `livekit.toml` Missing Agent Name

**What goes wrong:** `lk dispatch create --agent-name accountability-buddy` returns "agent not found."

**Why it happens:** `livekit.toml` either doesn't exist or has a different agent name than what's registered in `WorkerOptions(agent_name=...)`.

**How to avoid:** Ensure `WorkerOptions(agent_name="accountability-buddy")` and `livekit.toml` agent name match exactly.

**Warning signs:** Dispatch CLI returns error; worker log shows registration under a different name.

---

## Code Examples

### Complete Phase 1 agent.py Structure

```python
# Source: adapted from https://github.com/livekit-examples/outbound-caller-python (verified verbatim)

from __future__ import annotations
import asyncio
import logging
import os
from dotenv import load_dotenv
from livekit import api
from livekit.agents import (
    Agent, AgentSession, JobContext, RunContext,
    RoomInputOptions, function_tool, get_job_context,
    cli, WorkerOptions,
)
from livekit.plugins import openai, noise_cancellation

load_dotenv(dotenv_path=".env.local")
logger = logging.getLogger("accountability-buddy")
logger.setLevel(logging.INFO)

SYSTEM_PROMPT = """
You are an accountability coach calling for an evening habit check-in.
Your tone is direct, firm, and results-focused. You do not hedge or apologize.
This is a voice call — never use markdown, bullet points, or numbered lists.
Keep responses concise (under 3 sentences unless following up).
When the check-in is complete, say a direct goodbye and call end_call.
"""

class AccountabilityAgent(Agent):
    def __init__(self):
        super().__init__(instructions=SYSTEM_PROMPT)

    async def on_enter(self) -> None:
        # Speak first — agent initiates the conversation
        await self.session.generate_reply()

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

async def entrypoint(ctx: JobContext):
    await ctx.connect()

    session = AgentSession(
        llm=openai.realtime.RealtimeModel(voice="shimmer"),
    )

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
```

### .env.local

```bash
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=APIxxxxxxxxxxxxxxxx
LIVEKIT_API_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
OPENAI_API_KEY=sk-...
```

### pyproject.toml

```toml
[project]
name = "accountability-buddy"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "python-dotenv>=1.0",
    "livekit-agents[openai]~=1.4",
    "livekit-plugins-noise-cancellation~=0.2",
    "livekit>=1.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

### Development Workflow (Two Terminals)

```bash
# Terminal 1: Start the agent worker
uv run agent.py dev

# Terminal 2: Dispatch to test (after worker connects)
lk dispatch create --new-room --agent-name accountability-buddy --metadata '{}'
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| livekit-agents 0.x (MultimodalAgent) | livekit-agents 1.x (AgentSession + Agent) | April 2025 (1.0 release) | Different class hierarchy; 0.x tutorials are incompatible |
| Manual STT+LLM+TTS pipeline | `openai.realtime.RealtimeModel` single-hop | 2024 (OpenAI Realtime launch) | Eliminates 2 of 3 serial hops; lower latency |
| `WorkerOptions(entrypoint_fnc=...)` with `@agents.session()` | Both old and new pattern coexist | 1.0 | Both valid; official template still uses `WorkerOptions`; research mentions `@server.rtc_session` too |
| `silero.VAD` + explicit turn detection | Semantic VAD built into RealtimeModel | 2025 | No separate VAD plugin needed with Realtime |

**Deprecated/outdated:**
- LiveKit Agents 0.x `MultimodalAgent` class: replaced by `AgentSession + Agent` in 1.0. All tutorials predating April 2025 use the old API.
- `participant.disconnect_reason` for no-answer detection: known SDK bug #398, unreliable. Use `TwirpError` catch instead (Phase 2 concern, not Phase 1).

---

## Open Questions

1. **`on_enter` + `asyncio.create_task` interaction**
   - What we know: `on_enter()` is called when the agent becomes active in the session. `session_started` task completes when session is ready.
   - What's unclear: Exact timing — does `on_enter` fire during `session.start()` or after the awaited task resolves? If it fires during the task, `generate_reply()` must complete before the task resolves.
   - Recommendation: Test empirically in Plan 01-02. If `generate_reply()` in `on_enter` races with session start, add a short `await asyncio.sleep(0)` or move the call to after `await session_started`. The official template does NOT use `on_enter` — it uses manual `generate_reply` after participant joins. The Phase 1 browser test can use either approach; `on_enter` is simpler for Phase 1.

2. **`generate_reply()` with no instructions vs with instructions**
   - What we know: `generate_reply()` with no arguments uses the system prompt. `generate_reply(instructions="...")` overrides for a specific response.
   - What's unclear: Whether the system prompt alone produces a good enough opening for Phase 1 browser testing.
   - Recommendation: Start with `generate_reply(instructions="Greet the user and start the habit check-in.")` for predictable opening.

---

## Sources

### Primary (HIGH confidence)

- `outbound-caller-python` official example — complete verbatim source code reviewed; all patterns verified against this
  - URL: https://github.com/livekit-examples/outbound-caller-python
- LiveKit Outbound Calls doc — `create_sip_participant`, `TwirpError`, entrypoint pattern
  - URL: https://docs.livekit.io/telephony/making-calls/outbound-calls/
- OpenAI Realtime Plugin Guide — `RealtimeModel(voice=...)` configuration
  - URL: https://docs.livekit.io/agents/models/realtime/plugins/openai/
- `.planning/research/livekit-openai.md` — project-specific deep research with citations

### Secondary (MEDIUM confidence)

- PyPI livekit-agents — confirmed current stable version 1.4.4, released 2026-03-03
  - URL: https://pypi.org/project/livekit-agents/
- LiveKit Agents GitHub — architecture overview, agent_name dispatch behavior
  - URL: https://github.com/livekit/agents

### Tertiary (LOW confidence)

- None — all key patterns verified against official sources.

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — versions confirmed via PyPI; library choices from official template
- Architecture patterns: HIGH — entrypoint pattern, session ordering, end_call tool all verified against official template verbatim source
- Pitfalls: HIGH — agent_name dispatch behavior, BVCTelephony, playout-wait all confirmed in official docs and template source
- Open questions: Two minor timing questions that can be resolved empirically in implementation

**Research date:** 2026-03-10
**Valid until:** 2026-04-10 (livekit-agents 1.x active series; check for 1.5.0 stable release before then)
