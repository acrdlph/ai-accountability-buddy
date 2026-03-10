# Phase 2: Twilio SIP Telephony - Research

**Researched:** 2026-03-10
**Domain:** Twilio Elastic SIP Trunking + LiveKit SIP Service (outbound calls, voicemail detection, TwirpError handling)
**Confidence:** HIGH

---

## Summary

Phase 2 wires up Twilio Elastic SIP Trunking so the existing voice agent can call a real phone number. The work splits into two clean halves: (1) Twilio+LiveKit trunk configuration (entirely outside the codebase -- console/CLI work), and (2) Python code changes to the existing `agent.py` entrypoint to insert `create_sip_participant()` at the Phase 2 insertion point already marked in the code, plus adding `TwirpError` handling for no-answer and a `detected_answering_machine` function tool for voicemail.

The official `outbound-caller-python` example is the authoritative reference. It demonstrates the exact pattern this project needs: start the session in a background task, call `create_sip_participant()` with `wait_until_answered=True`, catch `api.TwirpError` for failure, then `await session_started` and `ctx.wait_for_participant()`. The voicemail detection pattern is LLM-driven -- a `@function_tool` with the docstring "Called when the call reaches voicemail. Use this tool AFTER you hear the voicemail greeting" that simply calls `hangup()`. No carrier-level AMD (Answering Machine Detection) is used; the LLM itself detects voicemail greetings via its audio understanding.

The critical detail for this phase is the `agent speaks first` behavior. The official LiveKit docs recommend that for outbound calls, the agent should let the callee speak first (to avoid talking over their "hello"). However, the Phase 2 success criteria explicitly require "agent speaks first" (FR6). The `on_enter` hook in the current `AccountabilityAgent` already handles this. Since `on_enter` fires when the agent becomes active in the session, and the session start is awaited after `create_sip_participant()` returns (meaning the phone has been answered), the timing should work correctly -- but this must be validated empirically.

**Primary recommendation:** Configure Twilio outbound trunk via CLI/console, register it in LiveKit as an outbound SIP trunk, then modify `agent.py` to insert `create_sip_participant()` at the existing Phase 2 comment, add `TwirpError` handling with `ctx.shutdown()`, and add a `detected_answering_machine` tool that hangs up. Store `SIP_OUTBOUND_TRUNK_ID` and `DEFAULT_PHONE_NUMBER` in `.env.local`.

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| FR1 | Daily Outbound Call -- system calls user's phone via Twilio SIP | `create_sip_participant()` with `wait_until_answered=True` via LiveKit SIP API; outbound trunk configured with Twilio credential-list auth |
| FR8 | Voicemail Detection -- agent detects voicemail greeting and hangs up | `detected_answering_machine` function tool pattern from official example; LLM-driven detection, not carrier AMD; system prompt instructs agent to use tool when voicemail detected |
| NFR4 | Single User -- hardcoded phone number configurable via env var | `DEFAULT_PHONE_NUMBER` env var; dispatch metadata passes `{"phone":"+1..."}` or falls back to env var; clean structure for future multi-user |
</phase_requirements>

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| livekit-agents[openai] | ~=1.4 (stable: 1.4.4) | Agent framework with SIP API access via `ctx.api.sip` | Already installed; `api.CreateSIPParticipantRequest` and `api.TwirpError` are in the `livekit` package |
| livekit | >=1.0 | Server SDK providing `api.CreateSIPParticipantRequest`, `api.TwirpError` | Already installed; provides all SIP types |
| Twilio Elastic SIP Trunking | N/A (service) | SIP trunk provider for PSTN connectivity | Project requirement; credential-list auth confirmed as correct approach |
| LiveKit CLI (`lk`) | latest | Create/manage outbound SIP trunks | Required for `lk sip outbound create` and `lk dispatch create` |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| Twilio CLI (`twilio`) | latest | Create SIP trunk and credential list on Twilio side | One-time setup; not needed at runtime |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| LLM-driven voicemail detection | Carrier-level AMD (Twilio AMD) | Carrier AMD adds latency and is configured at the Twilio level; LLM-based detection is simpler, works within LiveKit, and is the official recommended pattern |
| Credential-list auth | IP allowlisting | LiveKit Cloud nodes do not have static IPs -- credential-list auth is the only reliable option per official docs |
| `wait_until_answered=True` (sync) | Polling `sip.callStatus` attribute | Sync mode is simpler; blocks until answered or fails with TwirpError; no polling code needed |

**Installation:**

No new packages required. All SIP functionality is in the already-installed `livekit` package:
```python
from livekit import api
# api.CreateSIPParticipantRequest, api.TwirpError -- already available
```

---

## Architecture Patterns

### Recommended Project Structure

```
accountability-buddy/
├── .env.local              # Add: SIP_OUTBOUND_TRUNK_ID, DEFAULT_PHONE_NUMBER
├── pyproject.toml          # No changes needed
├── livekit.toml            # No changes needed
└── agent.py                # Modify: add SIP dial + TwirpError + voicemail tool
```

Single-file structure continues from Phase 1. No new files are needed.

### Pattern 1: Session Start, Then SIP Dial, Then Await Both

**What:** Start the agent session as a background task, then call `create_sip_participant()` to dial the phone, then await the session start and wait for the participant. This ordering ensures the agent is ready to handle audio the instant the call is answered.

**When to use:** Always for outbound calling. This is the exact pattern from the official outbound-caller-python example.

**Example:**

```python
# Source: https://github.com/livekit-examples/outbound-caller-python/blob/main/agent.py (adapted)

import json
import os
from livekit import api

outbound_trunk_id = os.getenv("SIP_OUTBOUND_TRUNK_ID")
default_phone = os.getenv("DEFAULT_PHONE_NUMBER")

async def entrypoint(ctx: JobContext):
    await ctx.connect()

    # Parse phone number from dispatch metadata, fall back to env var
    metadata = json.loads(ctx.job.metadata or "{}")
    phone_number = metadata.get("phone") or metadata.get("phone_number") or default_phone
    if not phone_number:
        logger.error("No phone number provided in metadata or DEFAULT_PHONE_NUMBER env var")
        ctx.shutdown()
        return

    agent = AccountabilityAgent()

    session = AgentSession(
        llm=openai.realtime.RealtimeModel(voice="shimmer"),
    )

    # 1. Start session in background FIRST
    session_started = asyncio.create_task(
        session.start(
            agent=agent,
            room=ctx.room,
            room_input_options=RoomInputOptions(
                noise_cancellation=noise_cancellation.BVCTelephony(),
            ),
        )
    )

    # 2. Dial the phone number
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

    # 3. Await session start and participant join
    await session_started
    participant = await ctx.wait_for_participant(identity=phone_number)
    logger.info(f"participant joined: {participant.identity}")
```

### Pattern 2: TwirpError Handling for No-Answer / Call Failure

**What:** `create_sip_participant()` with `wait_until_answered=True` blocks until the call is answered. If the call fails (no answer, busy, network error), it raises `api.TwirpError` with SIP status codes in `e.metadata`.

**When to use:** Always -- wrapping `create_sip_participant()` in a try/except is mandatory.

**Example:**

```python
# Source: https://docs.livekit.io/telephony/making-calls/outbound-calls/ (Python example)

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
        f"error creating SIP participant: {e.message}, "
        f"SIP status: {e.metadata.get('sip_status_code')} "
        f"{e.metadata.get('sip_status')}"
    )
    ctx.shutdown()
    return
```

**TwirpError properties:**
- `e.code`: Twirp error code (e.g., `"unknown"`, `"unavailable"`)
- `e.message`: Human-readable error message
- `e.status`: HTTP status code
- `e.metadata`: Dict with `sip_status_code` (SIP status like `"480"` for no answer) and `sip_status` (status message)

**Key SIP status codes for no-answer scenarios:**
- `480` -- Temporarily Unavailable (no answer, timeout)
- `486` -- Busy Here
- `487` -- Request Terminated (caller hung up during ring)
- `603` -- Decline

### Pattern 3: Voicemail Detection via LLM Function Tool

**What:** A `@function_tool` that the LLM calls when it detects a voicemail greeting. The tool simply hangs up. The system prompt instructs the agent to use this tool when it hears a voicemail greeting instead of a human voice.

**When to use:** Always for outbound calling. This is the official LiveKit pattern -- no carrier-level AMD involved.

**Example:**

```python
# Source: https://docs.livekit.io/telephony/making-calls/outbound-calls/#voicemail-detection
# Also: https://github.com/livekit-examples/outbound-caller-python/blob/main/agent.py

@function_tool()
async def detected_answering_machine(self, ctx: RunContext):
    """Called when the call reaches voicemail. Use this tool AFTER you hear the voicemail greeting"""
    logger.info("Voicemail detected -- hanging up")
    job_ctx = get_job_context()
    await job_ctx.api.room.delete_room(
        api.DeleteRoomRequest(room=job_ctx.room.name)
    )
```

**System prompt addition:**
```
If you hear a voicemail greeting or automated system instead of a real person,
immediately call the detected_answering_machine tool. Do not leave a message.
```

### Pattern 4: Environment-Based Configuration (NFR4)

**What:** Store the outbound trunk ID and default phone number in environment variables. The dispatch metadata can override the phone number, enabling future multi-user extension.

**When to use:** Always. Single-user phase uses env vars; multi-user phase will pass phone numbers via dispatch metadata.

```python
# .env.local additions
SIP_OUTBOUND_TRUNK_ID=ST_xxxxxxxxxxxx
DEFAULT_PHONE_NUMBER=+15105550100

# agent.py reads from env, with metadata override
outbound_trunk_id = os.getenv("SIP_OUTBOUND_TRUNK_ID")
default_phone = os.getenv("DEFAULT_PHONE_NUMBER")

# Dispatch metadata takes priority if present
metadata = json.loads(ctx.job.metadata or "{}")
phone_number = metadata.get("phone") or metadata.get("phone_number") or default_phone
```

### Anti-Patterns to Avoid

- **Omitting `wait_until_answered=True`:** Without this flag, `create_sip_participant()` returns immediately (before the call is answered). The agent session starts and `on_enter` fires before anyone is on the line, wasting the opening greeting.
- **Not catching `TwirpError`:** Unhandled `TwirpError` crashes the agent process. Always wrap `create_sip_participant()` in try/except.
- **Using carrier-level AMD instead of LLM detection:** Twilio AMD adds latency to call connection and is configured outside LiveKit. The official pattern uses the LLM's audio understanding.
- **Awaiting `session_started` before dialing:** This wastes time. The session should start in the background while the call dials. The official example starts session first, then dials.
- **Using `participant.disconnect_reason` for no-answer detection:** Known SDK bug #398 (documented in Phase 1 research). Use `TwirpError` catch instead.
- **Hardcoding trunk ID in code:** Use `SIP_OUTBOUND_TRUNK_ID` env var so trunk can be changed without code changes.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Outbound phone dialing | Custom SIP stack or Twilio REST API calls | `ctx.api.sip.create_sip_participant()` | LiveKit SIP Service handles SIP INVITE, codec negotiation, RTP media relay |
| No-answer detection | Polling participant attributes or custom timeout | `wait_until_answered=True` + `TwirpError` catch | Synchronous mode blocks until answer or failure; error includes SIP status codes |
| Voicemail detection | Audio analysis, silence detection, or carrier AMD | LLM `@function_tool` with voicemail instruction | LLM hears the greeting and decides; no signal processing code needed |
| Call hangup | Custom SIP BYE or session close | `delete_room()` (via EndCallTool or direct API) | Room deletion is the only mechanism that reliably disconnects SIP participants |
| SIP trunk auth | Custom auth middleware | Twilio credential-list + LiveKit outbound trunk config | Auth is handled at the SIP handshake level between LiveKit and Twilio |

**Key insight:** The entire telephony layer is configuration, not code. The only Python code changes are: (1) read trunk ID and phone from env, (2) call `create_sip_participant()` in a try/except, (3) add voicemail detection tool, and (4) update the system prompt.

---

## Common Pitfalls

### Pitfall 1: Twilio Credential List Not Associated with Trunk

**What goes wrong:** `create_sip_participant()` fails with a SIP 401/403 error. The call never reaches Twilio.

**Why it happens:** On Twilio, creating a credential list is not enough -- you must also associate it with the trunk under Termination > Authentication > Credential Lists. This is a two-step process that is easy to forget.

**How to avoid:** After creating the credential list in Twilio console, navigate to Elastic SIP Trunking > Manage > Trunks > [your trunk] > Termination > Authentication > Credential Lists and add the credential list. Verify by checking the trunk's termination settings.

**Warning signs:** TwirpError with SIP status code 401 or 403.

### Pitfall 2: Wrong Trunk Address Format

**What goes wrong:** Outbound trunk creation succeeds in LiveKit but calls fail with connection errors.

**Why it happens:** The `address` field in the LiveKit outbound trunk must be the Twilio termination SIP URI domain only (e.g., `my-trunk.pstn.twilio.com`), not a full SIP URI (no `sip:` prefix, no path).

**How to avoid:** Copy the termination URI from Twilio console > Elastic SIP Trunking > Manage > Trunks > [your trunk] > Termination. Use just the domain part.

**Warning signs:** TwirpError with connection-related error, SIP status 502 or 503.

### Pitfall 3: Agent Speaks Over the Caller's "Hello"

**What goes wrong:** The agent starts its greeting at the exact same moment the person says "hello," creating an awkward overlap.

**Why it happens:** `on_enter` fires immediately when the session becomes active. With `wait_until_answered=True`, the call is already connected when `session_started` resolves, so `on_enter` fires almost instantly after the person picks up.

**How to avoid:** This is the expected behavior per FR6 (agent speaks first). The `on_enter` hook with `generate_reply()` is correct. The RealtimeModel's semantic VAD handles interruption gracefully if both speak simultaneously. Test empirically and adjust system prompt if the opening feels too aggressive.

**Warning signs:** Users report the agent talks over their greeting. If this is a problem, add a brief `await asyncio.sleep(0.5)` before `generate_reply()` in `on_enter` -- but only if empirical testing shows it's needed.

### Pitfall 4: Missing Phone Number in Dispatch Metadata

**What goes wrong:** Agent starts but immediately shuts down because no phone number was provided.

**Why it happens:** `lk dispatch create` was called without `--metadata '{"phone":"+1..."}'` and `DEFAULT_PHONE_NUMBER` env var is not set.

**How to avoid:** Validate phone number early in the entrypoint. Log a clear error message. Fall back to `DEFAULT_PHONE_NUMBER` env var. The dispatch command for Phase 2 testing should always include metadata.

**Warning signs:** Agent log shows "No phone number provided" and shuts down immediately.

### Pitfall 5: Voicemail Tool Not Called Because System Prompt Doesn't Mention It

**What goes wrong:** The agent reaches voicemail and starts having a "conversation" with the voicemail greeting, then leaves an unintended message.

**Why it happens:** The system prompt doesn't instruct the agent to detect voicemail and call `detected_answering_machine`. The LLM doesn't know the tool exists for this purpose unless told.

**How to avoid:** Add explicit voicemail detection instructions to the system prompt: "If you hear a voicemail greeting or automated system instead of a real person, immediately call the detected_answering_machine tool. Do not leave a message."

**Warning signs:** Call logs show the agent speaking to voicemail systems for extended periods.

### Pitfall 6: Default Timeout Too Short for Ringing

**What goes wrong:** `create_sip_participant()` times out before the phone finishes ringing.

**Why it happens:** The `aiohttp` session default timeout may be shorter than the typical ring time (15-30 seconds). The SDK automatically extends to 20s when `wait_until_answered=True`, but this may still be tight.

**How to avoid:** The `ringing_timeout` parameter on `CreateSIPParticipantRequest` controls the maximum ring time (upper limit 80 seconds). The SDK's `create_sip_participant()` also accepts a `timeout` kwarg in seconds. For this project, the default behavior (SDK auto-extends to 20s) should be sufficient, but set `ringing_timeout` to 30 seconds explicitly if testing reveals premature timeouts.

**Warning signs:** TwirpError before the phone has rung long enough for a person to pick up.

---

## Code Examples

### Complete Phase 2 agent.py Modifications

```python
# Source: adapted from https://github.com/livekit-examples/outbound-caller-python/blob/main/agent.py
# Applied to existing Phase 1 agent.py structure

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
- Keep responses concise -- under 3 sentences unless following up on a specific habit.
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
        """Agent speaks first -- initiate the accountability check-in immediately."""
        await self.session.generate_reply(
            instructions="Greet the user and kick off the accountability check-in. Be direct -- no small talk."
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
```

### Twilio SIP Trunk Setup (CLI Commands)

```bash
# Step 1: Create Twilio SIP trunk
twilio api trunking v1 trunks create \
  --friendly-name "accountability-buddy" \
  --domain-name "accountability-buddy.pstn.twilio.com"
# Copy the trunk SID from output

# Step 2: Create credential list (Twilio console or CLI)
# Navigate to: Twilio Console > Voice > Credential Lists
# Create credentials with a username and password of your choice

# Step 3: Associate credential list with trunk termination
# Navigate to: Elastic SIP Trunking > Manage > Trunks > accountability-buddy
# Go to: Termination > Authentication > Credential Lists
# Select and save the credential list

# Step 4: Associate phone number with trunk
twilio api trunking v1 trunks phone-numbers create \
  --trunk-sid <twilio_trunk_sid> \
  --phone-number-sid <twilio_phone_number_sid>
```

### LiveKit Outbound Trunk Setup (CLI)

```bash
# Create outbound-trunk.json:
# {
#   "trunk": {
#     "name": "accountability-buddy-outbound",
#     "address": "accountability-buddy.pstn.twilio.com",
#     "numbers": ["+1YOUR_TWILIO_NUMBER"],
#     "authUsername": "<credential-list-username>",
#     "authPassword": "<credential-list-password>"
#   }
# }

lk sip outbound create outbound-trunk.json
# Output: SIPTrunkID: ST_xxxxxxxxxxxx
# Add this to .env.local as SIP_OUTBOUND_TRUNK_ID

# Verify trunk was created:
lk sip outbound list
```

### Dispatch Command for Testing

```bash
# Test with explicit phone number in metadata:
lk dispatch create \
  --new-room \
  --agent-name accountability-buddy \
  --metadata '{"phone":"+15105550100"}'

# Test with DEFAULT_PHONE_NUMBER env var (empty metadata):
lk dispatch create \
  --new-room \
  --agent-name accountability-buddy \
  --metadata '{}'
```

### .env.local Additions

```bash
# Existing Phase 1 vars:
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=APIxxxxxxxxxxxxxxxx
LIVEKIT_API_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
OPENAI_API_KEY=sk-...

# New Phase 2 vars:
SIP_OUTBOUND_TRUNK_ID=ST_xxxxxxxxxxxx
DEFAULT_PHONE_NUMBER=+15105550100
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `create_sip_trunk()` (unified inbound+outbound) | `create_sip_outbound_trunk()` / `create_sip_inbound_trunk()` (separated) | 2025 | Old unified method deprecated; use direction-specific methods |
| `create_sip_outbound_trunk()` | `create_outbound_trunk()` (renamed) | Recent | Old name deprecated with warning; both work but prefer new name for trunk creation |
| Carrier-level AMD (Twilio AMD) | LLM-driven voicemail detection via function tool | 2025 (LiveKit Agents 1.x) | No carrier config needed; LLM detects voicemail by listening to audio |
| `participant.disconnect_reason` for no-answer | `TwirpError` catch with `sip_status_code` metadata | Known bug #398 | disconnect_reason unreliable; TwirpError is the correct pattern |

**Deprecated/outdated:**
- `create_sip_outbound_trunk()` method name: still works but raises deprecation warning. Use `create_outbound_trunk()` for trunk management (not relevant at runtime -- trunk is created once via CLI).
- `SIPTrunkInfo` (unified type): deprecated in favor of `SIPInboundTrunkInfo` and `SIPOutboundTrunkInfo`.

---

## Open Questions

1. **Agent speaks first timing with SIP**
   - What we know: `on_enter()` fires when the agent becomes active in the session. With `wait_until_answered=True`, `create_sip_participant()` returns only after the phone is answered. Then `await session_started` completes and the participant joins.
   - What's unclear: Exact timing of `on_enter` relative to the SIP participant's audio being ready. If `on_enter` fires before the SIP participant's audio track is subscribed, the greeting might not be heard.
   - Recommendation: Test empirically. If the greeting is missed, move `generate_reply()` to after `ctx.wait_for_participant()` instead of using `on_enter`. The official outbound-caller example does NOT use `on_enter` -- it relies on the LLM to start the conversation naturally.

2. **Voicemail detection reliability**
   - What we know: The LLM-based approach relies on OpenAI Realtime's audio understanding to recognize voicemail greetings. The official example uses this pattern.
   - What's unclear: How reliably the LLM distinguishes between a voicemail greeting and a human saying "hello." False positives (hanging up on a real person) would be worse than false negatives (talking to voicemail briefly).
   - Recommendation: Make the system prompt instruction specific: "AFTER hearing a complete voicemail greeting" to reduce false positives. Accept some false negatives as tolerable. Monitor call logs during testing.

3. **`ringing_timeout` default behavior**
   - What we know: The SDK auto-extends the aiohttp timeout to 20s when `wait_until_answered=True`. The SIP API supports `ringing_timeout` up to 80 seconds.
   - What's unclear: Whether 20 seconds is enough ring time for all scenarios (some phones ring for 25-30 seconds before going to voicemail).
   - Recommendation: Start with defaults. If testing shows premature timeouts, add `ringing_timeout` with a `google.protobuf.Duration` set to 30 seconds. The SDK `timeout` kwarg can also be increased.

---

## Sources

### Primary (HIGH confidence)

- LiveKit Outbound Calls documentation -- `create_sip_participant()`, `TwirpError`, voicemail detection, agent-initiated outbound calls
  - URL: https://docs.livekit.io/telephony/making-calls/outbound-calls/
- LiveKit SIP API Reference -- `CreateSIPParticipantRequest` fields, `CreateSIPOutboundTrunk`, all parameter definitions
  - URL: https://docs.livekit.io/reference/telephony/sip-api/
- LiveKit Twilio SIP Trunk Setup -- step-by-step credential-list auth configuration
  - URL: https://docs.livekit.io/telephony/start/providers/twilio/
- LiveKit SIP Outbound Trunk documentation -- trunk creation with Twilio address format, auth config
  - URL: https://docs.livekit.io/telephony/making-calls/outbound-trunk/
- Official `outbound-caller-python` example -- complete reference implementation (verbatim source reviewed)
  - URL: https://github.com/livekit-examples/outbound-caller-python/blob/main/agent.py
- LiveKit SIP Participant Reference -- `sip.callStatus` attributes, Twilio-specific attributes
  - URL: https://docs.livekit.io/reference/telephony/sip-participant/
- LiveKit Python SDK `TwirpError` source -- `code`, `message`, `status`, `metadata` properties
  - URL: https://github.com/livekit/python-sdks/blob/main/livekit-api/livekit/api/twirp_client.py
- LiveKit Python SDK `SipService.create_sip_participant()` source -- timeout handling, `wait_until_answered` logic
  - URL: https://github.com/livekit/python-sdks/blob/main/livekit-api/livekit/api/sip_service.py
- LiveKit EndCallTool documentation -- prebuilt tool for graceful call termination
  - URL: https://docs.livekit.io/agents/prebuilt/tools/end-call-tool/

### Secondary (MEDIUM confidence)

- LiveKit IP address note for outbound auth -- "LiveKit Cloud nodes do not have a static IP address range" confirming credential-list auth is required
  - URL: https://docs.livekit.io/telephony/making-calls/outbound-trunk/ (bottom of page)

### Tertiary (LOW confidence)

- None -- all patterns verified against official documentation and example source code.

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new packages needed; all SIP functionality verified in existing `livekit` package source
- Architecture patterns: HIGH -- all patterns come directly from official outbound-caller-python example (verbatim source reviewed) and official LiveKit docs
- Pitfalls: HIGH -- credential-list association, trunk address format, TwirpError handling all documented in official sources; disconnect_reason bug from Phase 1 research
- Twilio setup: HIGH -- step-by-step instructions from official LiveKit Twilio provider guide
- Voicemail detection: MEDIUM -- official pattern is clear, but LLM reliability for distinguishing voicemail vs. human is an empirical question

**Research date:** 2026-03-10
**Valid until:** 2026-04-10 (SIP API is stable; check for `livekit-agents` 1.5.0 stable release before then)
