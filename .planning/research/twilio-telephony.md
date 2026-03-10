# Twilio + LiveKit Telephony Integration

**Researched:** 2026-03-10
**Confidence:** HIGH (primary sources: LiveKit official docs, official Python example repo)

---

## Executive Summary

LiveKit handles Twilio telephony through its SIP bridge layer. You configure a Twilio Elastic SIP Trunk, point it at your LiveKit SIP endpoint, then call `create_sip_participant()` from within an agent to initiate outbound calls. The agent dispatches first (creating or joining a room), then dials the user's phone. Audio flows: OpenAI Realtime ↔ LiveKit Agent ↔ LiveKit Room ↔ LiveKit SIP Service ↔ Twilio Elastic SIP Trunk ↔ PSTN ↔ User's phone.

The official `outbound-caller-python` example matches this project's stack exactly (Python, OpenAI Realtime, SIP outbound) and should be the primary reference for implementation.

---

## 1. How LiveKit's Twilio/SIP Integration Works

LiveKit operates a dedicated SIP service (`livekit.cloud` SIP URI) that bridges standard SIP telephony to LiveKit rooms. When a call is placed:

1. `CreateSIPParticipant` API is called → LiveKit SIP sends a SIP INVITE to Twilio's elastic trunk
2. Twilio validates credentials and routes to the PSTN
3. User's phone rings
4. On answer, a SIP participant appears in the LiveKit room as a regular participant
5. Audio flows bidirectionally between the phone and all other room participants (including the AI agent)

The SIP participant behaves like any other LiveKit participant — the agent audio track streams to them, and their audio track streams back to the agent.

**Key constraint:** LiveKit Cloud nodes do not have static IP addresses, so IP-allowlist authentication with Twilio won't work. Use username/password authentication instead.

Source: [LiveKit Telephony Introduction](https://docs.livekit.io/telephony/) | [Outbound Trunk Setup](https://docs.livekit.io/telephony/making-calls/outbound-trunk/)

---

## 2. Twilio SIP Trunk Setup (Step by Step)

### Twilio Side

**Step 1: Create Elastic SIP Trunk**
```bash
twilio api trunking v1 trunks create \
  --friendly-name "LiveKit Accountability Buddy" \
  --domain-name "accountability-buddy.pstn.twilio.com"
```
Save the Trunk SID (`TKxxx...`). Domain name must end in `.pstn.twilio.com`.

**Step 2: Configure Origination URI (for inbound — needed even for outbound-only)**
```bash
twilio api trunking v1 trunks origination-urls create \
  --trunk-sid <TRUNK_SID> \
  --friendly-name "LiveKit SIP URI" \
  --sip-url "sip:<your-livekit-sip-endpoint>;transport=tcp" \
  --weight 1 --priority 1 --enabled
```
Your LiveKit SIP endpoint is found in the LiveKit Cloud dashboard. It looks like `vjnxecm0tjk.sip.livekit.cloud`.

**Step 3: Create Credential List (for outbound authentication)**
- Go to Twilio Console → Voice → Credential lists
- Create a new list with a username and password of your choice
- Then: Twilio Console → Elastic SIP Trunking → Your Trunk → Termination → Authentication → Credential Lists → add your list

**Step 4: Associate Phone Number**
```bash
twilio api trunking v1 trunks phone-numbers create \
  --trunk-sid <TRUNK_SID> \
  --phone-number-sid <PHONE_NUMBER_SID>
```

### LiveKit Side

**Create an Outbound Trunk** (via LiveKit Cloud Dashboard → Telephony → SIP trunks → Create → Outbound):

```json
{
  "name": "Twilio Outbound",
  "address": "accountability-buddy.pstn.twilio.com",
  "numbers": ["+1XXXXXXXXXX"],
  "authUsername": "<same-username-from-twilio-credential-list>",
  "authPassword": "<same-password-from-twilio-credential-list>"
}
```

Or via Python SDK:
```python
from livekit import api
from livekit.protocol.sip import CreateSIPOutboundTrunkRequest, SIPOutboundTrunkInfo

async def create_trunk():
    lkapi = api.LiveKitAPI()
    trunk = SIPOutboundTrunkInfo(
        name="Twilio Outbound",
        address="accountability-buddy.pstn.twilio.com",
        numbers=["+1XXXXXXXXXX"],
        auth_username="<username>",
        auth_password="<password>",
    )
    request = CreateSIPOutboundTrunkRequest(trunk=trunk)
    result = await lkapi.sip.create_sip_outbound_trunk(request)
    print("Trunk ID:", result.sip_trunk_id)  # Save this as SIP_OUTBOUND_TRUNK_ID
    await lkapi.aclose()
```

Save the resulting trunk ID as the `SIP_OUTBOUND_TRUNK_ID` environment variable.

Source: [Twilio SIP Trunk Setup](https://docs.livekit.io/telephony/start/providers/twilio/) | [Outbound Trunk Docs](https://docs.livekit.io/telephony/making-calls/outbound-trunk/)

---

## 3. Programmatic Outbound Call Initiation

The canonical pattern for agent-initiated outbound calls has two phases:

### Phase 1: Dispatch the Agent (from cron job / external trigger)

```python
import asyncio
import json
from livekit import api

async def trigger_accountability_call(phone_number: str):
    """Called by cron job at 7pm. Dispatches agent to a new room."""
    lkapi = api.LiveKitAPI(
        url=LIVEKIT_URL,
        api_key=LIVEKIT_API_KEY,
        api_secret=LIVEKIT_API_SECRET,
    )
    dispatch = await lkapi.agent_dispatch.create_dispatch(
        api.CreateAgentDispatchRequest(
            agent_name="accountability-buddy",   # must match WorkerOptions agent_name
            room="accountability-" + phone_number,  # unique room per call
            metadata=json.dumps({"phone_number": phone_number}),
        )
    )
    await lkapi.aclose()
    return dispatch
```

### Phase 2: Agent Entrypoint — Connect then Dial

```python
import asyncio
import json
import logging
from livekit import api
from livekit.agents import JobContext, WorkerOptions, cli

logger = logging.getLogger(__name__)

async def entrypoint(ctx: JobContext):
    # 1. Agent connects to the room first
    await ctx.connect()

    # 2. Parse phone number from dispatch metadata
    dial_info = json.loads(ctx.job.metadata)
    phone_number = dial_info["phone_number"]

    # 3. Dial the user
    try:
        await ctx.api.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                room_name=ctx.room.name,
                sip_trunk_id=SIP_OUTBOUND_TRUNK_ID,
                sip_call_to=phone_number,
                participant_identity=phone_number,
                participant_name="User",
                wait_until_answered=True,       # blocks until answered (or timeout)
                ringing_timeout=timedelta(seconds=30),  # max 80s
                play_dialtone=False,
                krisp_enabled=True,             # noise cancellation
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

    # 4. Start agent session (OpenAI Realtime or VoicePipeline)
    # ... (see ARCHITECTURE.md for agent session details)

if __name__ == "__main__":
    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name="accountability-buddy",
    ))
```

**Critical ordering:** `ctx.connect()` must come BEFORE `create_sip_participant()`. The agent must be in the room before dialing so it can receive the SIP participant's audio track when the call connects.

Source: [Making Outbound Calls Quickstart](https://docs.livekit.io/agents/quickstarts/outbound-calls/) | [outbound-caller-python example](https://github.com/livekit-examples/outbound-caller-python) | [Agent Dispatch Docs](https://docs.livekit.io/agents/server/agent-dispatch/)

---

## 4. Twilio Configuration Checklist

| Item | What You Need |
|------|---------------|
| Twilio account | Standard account with Elastic SIP Trunking enabled |
| Phone number | Purchased US number from Twilio, associated with the SIP trunk |
| Elastic SIP Trunk | Domain: `<name>.pstn.twilio.com`, with credential list for auth |
| Credential list | Username + password (no IP allowlisting — LiveKit IPs are not static) |
| LiveKit Cloud | Project with SIP enabled (check Telephony tab in dashboard) |
| Outbound trunk | Created in LiveKit pointing to `<name>.pstn.twilio.com` with matching credentials |
| Env vars | `SIP_OUTBOUND_TRUNK_ID`, `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` |

**Twilio account prerequisites:**
- Elastic SIP Trunking product enabled (not just basic Voice)
- A "long code" (regular 10-digit US number) associated with the trunk
- Termination URI configured with your credential list

Source: [Twilio SIP Trunk Setup for LiveKit](https://docs.livekit.io/telephony/start/providers/twilio/)

---

## 5. Audio Flow

```
OpenAI Realtime API
       |
       | (WebSocket / Realtime audio)
       |
LiveKit Agent (Python process)
       |
       | (LiveKit WebRTC audio track)
       |
LiveKit Room (livekit.cloud)
       |
       | (LiveKit Room ↔ SIP bridge)
       |
LiveKit SIP Service (livekit.cloud SIP endpoint)
       |
       | (SIP INVITE + RTP audio)
       | Protocol: SIP/TLS for signaling, SRTP for media
       | Codec: PCMU (G.711 µ-law, 8kHz) — standard PSTN
       |        G.722 if Twilio negotiates HD voice
       |
Twilio Elastic SIP Trunk
       |
       | (PSTN)
       |
User's Phone
```

**Codec details:**
- LiveKit SIP supports PCMU, PCMA, and G.722
- Twilio supports PCMU, PCMA (and Opus/G.729 if enabled on account)
- Default negotiation will land on PCMU (8kHz, standard phone quality)
- G.722 (HD voice, 16kHz) is possible if both sides support it
- No video over SIP (not supported by LiveKit SIP)

**Audio quality note:** PCMU at 8kHz is "telephone quality" — adequate for voice AI conversations but noticeably lower fidelity than the agent's internal processing. This is unavoidable with PSTN calls.

Source: [LiveKit Codec Negotiation](https://docs.livekit.io/reference/telephony/codecs-negotiation/) | [Twilio SIP Codecs](https://www.twilio.com/docs/sip-trunking/codecs)

---

## 6. Call Event Handling

### SIP Participant Attributes

Monitor these on the SIP participant after calling `create_sip_participant()`:

| Attribute | Values | Meaning |
|-----------|--------|---------|
| `sip.callStatus` | `dialing` | Call is being placed |
| `sip.callStatus` | `active` | User answered — call is live |
| `sip.callStatus` | `hangup` | Call ended |
| `sip.callID` | string | LiveKit's unique call ID |
| `sip.phoneNumber` | string | The user's phone number |
| `sip.trunkID` | string | Which trunk was used |

### Disconnect Reasons

When a SIP participant disconnects (leaves the room), check `participant.disconnect_reason`:

| Value | Meaning | Action |
|-------|---------|--------|
| `DisconnectReason.USER_UNAVAILABLE` | No answer — ringing timed out | Schedule retry |
| `DisconnectReason.USER_REJECTED` | Busy (SIP 486 BUSY_HERE) | Schedule retry |
| `DisconnectReason.SIP_TRUNK_FAILURE` | SIP protocol error | Log error, skip retry |
| `api.TwirpError` | Trunk auth failure, bad number | Log, alert |

### Event Listening Pattern

```python
@ctx.room.on("participant_attributes_changed")
def on_attributes_changed(changed_attributes: dict, participant):
    if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
        call_status = participant.attributes.get("sip.callStatus")
        if call_status == "active":
            logger.info("Call answered — starting conversation")
        elif call_status == "hangup":
            logger.info("Call ended by participant")

@ctx.room.on("participant_disconnected")
def on_participant_disconnected(participant):
    if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
        reason = participant.disconnect_reason
        if reason == rtc.DisconnectReason.USER_UNAVAILABLE:
            logger.info("No answer — will retry in 30 minutes")
            # schedule_retry()
        elif reason == rtc.DisconnectReason.USER_REJECTED:
            logger.info("Busy — will retry in 30 minutes")
            # schedule_retry()
```

### Voicemail Detection

LiveKit has no automatic voicemail detection. The recommended approach is to give the LLM a tool to call when it detects a voicemail greeting:

```python
@function_tool()
async def detected_answering_machine(self):
    """Called when the agent hears a voicemail greeting instead of a live person."""
    logger.info("Voicemail detected — hanging up")
    await self.hangup()
```

The agent's LLM will recognize automated voicemail greetings ("Hi, you've reached...") and call this tool. This is soft detection — the LLM decides, not a signal from the carrier.

**Known SDK issue:** As of late 2025, `participant.disconnect_reason` and `sip.callStatus` have been reported as unreliable in some SDK versions (Python SDK issue #398, closed as "not planned"). Use `wait_until_answered=True` + TwirpError exception handling as the primary signal for failed calls, and monitor room events as a secondary check.

Source: [SIP Participant Attributes](https://docs.livekit.io/reference/telephony/sip-participant/) | [SIP Lifecycle Recipe](https://docs.livekit.io/recipes/sip_lifecycle/) | [disconnect_reason issue](https://github.com/livekit/python-sdks/issues/398)

---

## 7. Retry Pattern (No-Answer)

The project requires: retry once, 30 minutes after no answer.

### Recommended Approach: External Scheduler

Since the LiveKit agent shuts down after a failed/missed call, retry must be orchestrated externally:

```python
import asyncio
from datetime import datetime, timedelta

async def attempt_call(phone_number: str, attempt: int = 1):
    """Trigger one call attempt via agent dispatch."""
    try:
        await dispatch_accountability_call(phone_number)
        # On success the agent handles the conversation
        # On no-answer, the agent shuts down — we detect via job completion webhook
    except Exception as e:
        logger.error(f"Dispatch failed: {e}")

async def daily_call_with_retry(phone_number: str):
    """Main cron entry point — initiates call with one retry."""
    # First attempt
    await attempt_call(phone_number, attempt=1)

    # Wait for job completion (webhook or polling)
    result = await wait_for_call_result(timeout_seconds=120)

    if result == "no_answer":
        logger.info("No answer — scheduling retry in 30 minutes")
        await asyncio.sleep(30 * 60)
        await attempt_call(phone_number, attempt=2)
    elif result == "answered":
        logger.info("Call completed successfully")
    elif result == "voicemail":
        logger.info("Voicemail detected — no retry (agent left no message)")
```

### Detecting No-Answer from Outside the Agent

The agent signals no-answer by calling `ctx.shutdown()` after catching a failed SIP call. The cron scheduler can detect this via:

1. **LiveKit Webhooks** — subscribe to `job_completed` webhook; inspect metadata for outcome
2. **Simple timeout** — if the job completes in under N seconds, it was likely no-answer
3. **Agent-set room metadata** — agent writes outcome to room data before shutting down, cron reads it

**Simplest viable pattern for a single-user system:**

```python
# In agent — write outcome before shutdown
await ctx.room.local_participant.update_metadata(
    json.dumps({"outcome": "no_answer"})
)
ctx.shutdown()
```

```python
# In cron — wait, then check room metadata or job status
# Use LiveKit REST API to query room participant metadata after job completes
```

### `wait_until_answered` Timing

When `wait_until_answered=True` with `ringing_timeout=timedelta(seconds=30)`:
- If answered: returns normally, agent continues
- If not answered in 30s: raises `api.TwirpError`
- Maximum `ringing_timeout` is 80 seconds (LiveKit enforced limit)

This makes the no-answer case deterministic — catch `TwirpError` and initiate the retry schedule.

```python
try:
    await ctx.api.sip.create_sip_participant(
        api.CreateSIPParticipantRequest(
            ...
            wait_until_answered=True,
            ringing_timeout=timedelta(seconds=30),
        )
    )
    # Answered — continue with conversation
except api.TwirpError as e:
    sip_code = e.metadata.get("sip_status_code")
    logger.warning(f"Call not answered. SIP {sip_code}: {e.metadata.get('sip_status')}")
    # Signal to external orchestrator: no answer
    await ctx.room.local_participant.update_metadata(
        json.dumps({"outcome": "no_answer", "sip_code": sip_code})
    )
    ctx.shutdown()
```

Source: [outbound-caller-python example](https://github.com/livekit-examples/outbound-caller-python) | [Make Outbound Calls](https://docs.livekit.io/telephony/making-calls/outbound-calls/)

---

## Environment Variables Required

```bash
# LiveKit
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=APIxxxxxxxxxxxxxxxx
LIVEKIT_API_SECRET=<secret>

# SIP
SIP_OUTBOUND_TRUNK_ID=ST_xxxxxxxxxxxxxxxxxxxx

# OpenAI
OPENAI_API_KEY=sk-...

# User config
USER_PHONE_NUMBER=+1XXXXXXXXXX
CALL_TIME_HOUR=19  # 7pm in 24h format
```

---

## Official Example to Reference

LiveKit maintains an official Python outbound caller example that uses OpenAI Realtime — exactly this project's stack:

```bash
# Bootstrap from official template
lk app create --template=outbound-caller-python
```

Repository: https://github.com/livekit-examples/outbound-caller-python

It includes:
- Agent class with `detected_answering_machine` tool
- `end_call` and `transfer_call` tools
- Krisp noise cancellation
- OpenAI Realtime + VoicePipeline fallback
- `wait_until_answered=True` pattern
- Error handling with `TwirpError`

The accountability buddy agent should start from this template and replace the dental appointment logic with the Habitify habit-checking logic.

---

## Key Pitfalls

### 1. IP Allowlisting Won't Work with LiveKit Cloud
LiveKit Cloud nodes are not on static IPs. Always use username/password authentication on your Twilio credential list. Attempting IP-based auth will result in 403 errors.

### 2. Agent Must Connect Before Dialing
`ctx.connect()` must be called before `create_sip_participant()`. If you dial first, the SIP participant arrives in the room with no agent to receive their audio.

### 3. Twilio Domain Format
The Twilio SIP trunk domain in LiveKit's outbound trunk `address` field must be `<name>.pstn.twilio.com` (no `sip:` prefix, no subdomain). Using the wrong format causes 503 errors.

### 4. disconnect_reason Reliability
Python SDK `participant.disconnect_reason` has known reliability issues (closed issue, not fixed). Use `TwirpError` exception handling as the primary no-answer signal when `wait_until_answered=True`.

### 5. Voicemail Detection is LLM-Based
There is no carrier-level AMD (answering machine detection) via LiveKit SIP. The LLM must recognize the voicemail greeting and call the `detected_answering_machine` tool. This works in practice but could theoretically fail on unusual voicemail greetings.

### 6. ringing_timeout Max is 80 Seconds
LiveKit enforces a maximum of 80 seconds for `ringing_timeout`. Standard voicemail picks up after ~25-30 seconds. Setting 30 seconds means the agent hangs up just as voicemail might answer — consider 45s if you want voicemail detection instead of just hanging up.

---

## Sources

- [Making Outbound Calls (quickstart)](https://docs.livekit.io/agents/quickstarts/outbound-calls/)
- [Twilio SIP Trunk Setup for LiveKit](https://docs.livekit.io/telephony/start/providers/twilio/)
- [SIP Trunk Setup Overview](https://docs.livekit.io/telephony/start/sip-trunk-setup/)
- [Outbound Trunk Configuration](https://docs.livekit.io/telephony/making-calls/outbound-trunk/)
- [Outbound Call Workflow & Setup](https://docs.livekit.io/telephony/making-calls/workflow-setup/)
- [Make Outbound Calls](https://docs.livekit.io/telephony/making-calls/outbound-calls/)
- [SIP Participant Attributes Reference](https://docs.livekit.io/reference/telephony/sip-participant/)
- [SIP APIs Reference](https://docs.livekit.io/sip/api/)
- [SIP Lifecycle Management Recipe](https://docs.livekit.io/recipes/sip_lifecycle/)
- [Agent Dispatch Documentation](https://docs.livekit.io/agents/server/agent-dispatch/)
- [Audio Codecs Negotiation](https://docs.livekit.io/reference/telephony/codecs-negotiation/)
- [SIP Troubleshooting](https://docs.livekit.io/sip/troubleshooting/)
- [outbound-caller-python (official example)](https://github.com/livekit-examples/outbound-caller-python)
- [Twilio SIP Trunking Codecs](https://www.twilio.com/docs/sip-trunking/codecs)
- [Python SDK disconnect_reason issue #398](https://github.com/livekit/python-sdks/issues/398)
