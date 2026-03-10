# Research: Scheduling, Deployment, and End-to-End Orchestration

**Domain:** LiveKit voice agent with scheduled outbound phone calls
**Researched:** 2026-03-10
**Overall confidence:** HIGH (official LiveKit docs + working example repos verified)

---

## 1. LiveKit Agent Deployment Options

### Option A: LiveKit Cloud (Recommended for personal project)

LiveKit Cloud is the simplest path. Deploy with a single CLI command. No infrastructure to manage.

**Free tier:** 1,000 agent session minutes/month (Build plan, no credit card required). For a single daily call of ~15 minutes, that is ~450 minutes/month — well within the free tier.

**How it works:**
- LiveKit Cloud builds a container image from your code and Dockerfile
- They inject `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` at runtime (do NOT set these yourself)
- You provide the other secrets (OpenAI, Twilio) via their secrets manager
- Automatic scaling, load balancing, observability included

**Deploy command:**
```bash
lk cloud deploy
```

Source: [LiveKit Cloud deployment](https://docs.livekit.io/deploy/agents/), [Pricing](https://livekit.com/pricing)

---

### Option B: Self-Hosted Docker (Simplest self-hosted path)

Agents are just Docker containers that open an outbound WebSocket connection to LiveKit server. No inbound ports needed.

**Minimal Dockerfile:**
```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

CMD ["python", "agent.py", "start"]
```

**Environment variables for self-hosted:**
```
LIVEKIT_URL=wss://your-livekit-server.livekit.cloud
LIVEKIT_API_KEY=your-api-key
LIVEKIT_API_SECRET=your-api-secret
OPENAI_API_KEY=...
SIP_OUTBOUND_TRUNK_ID=ST_xxxxxxxx
```

**Hardware:** 4 cores / 8GB RAM handles 10-25 concurrent jobs. For a personal project making one call at a time, a 1 vCPU / 512MB instance (e.g., Fly.io smallest VM or Render free tier) is sufficient.

Source: [Self-hosted deployments](https://docs.livekit.io/deploy/custom/deployments/), [agent-deployment examples](https://github.com/livekit-examples/agent-deployment)

---

### Recommendation for This Project

**Use LiveKit Cloud (free tier) + a separate lightweight scheduler.**

Rationale:
- Zero infrastructure overhead
- 1,000 free agent minutes covers a 15-minute daily call for the entire month
- No need to self-host the LiveKit server component
- The agent worker process itself runs wherever you want (local machine, any VPS, or LiveKit Cloud)

---

## 2. Triggering an Agent on a Schedule (Not in Response to User)

This is the key architectural question. LiveKit agents normally wait in a pool for dispatch requests. For a scheduled outbound call, you flip the model: **an external scheduler calls the LiveKit API to create a dispatch and a SIP participant.**

### The Two-Part Trigger

**Part 1: Dispatch the agent to a room**
```python
from livekit import api

lkapi = api.LiveKitAPI()

# Room is auto-created if it does not exist
dispatch = await lkapi.agent_dispatch.create_dispatch(
    api.CreateAgentDispatchRequest(
        agent_name="accountability-buddy",    # must match agent name in code
        room="daily-checkin-room",
        metadata='{"phone_number": "+15105550123"}'
    )
)
```

**Part 2: The agent entrypoint creates the SIP participant (makes the call)**
```python
@agent.entrypoint
async def entrypoint(ctx: JobContext):
    dial_info = json.loads(ctx.job.metadata)
    phone_number = dial_info["phone_number"]

    # Dial the user's phone
    await ctx.api.sip.create_sip_participant(
        api.CreateSIPParticipantRequest(
            room_name=ctx.room.name,
            sip_trunk_id=os.environ["SIP_OUTBOUND_TRUNK_ID"],
            sip_call_to=phone_number,
            participant_identity="user-phone",
            wait_until_answered=True,   # block until answered
        )
    )
    # ... then set up the agent session for conversation
```

**Explicit dispatch is required** for telephony agents. Giving the agent a `name` in code disables automatic dispatch (agents won't fire on every room creation). This is LiveKit's recommendation for all telephony use cases.

Source: [Agent dispatch](https://docs.livekit.io/agents/server/agent-dispatch/), [outbound-caller-python example](https://github.com/livekit-examples/outbound-caller-python)

---

## 3. Scheduling Options

### Option A: System cron (simplest, recommended)

If the agent worker runs on a VPS or your Mac 24/7, use crontab to call a trigger script daily at 7pm.

```
# crontab -e
0 19 * * * /usr/bin/python3 /path/to/trigger.py
```

The trigger script calls the LiveKit API to dispatch the agent. The agent worker process must already be running and connected to LiveKit Cloud.

**Pros:** Zero dependencies, no extra library, universally understood.
**Cons:** Requires the host machine to be running at 7pm. No retry on failure.

---

### Option B: APScheduler in Python (good for single-process design)

Embed the scheduler directly in the same Python process as the agent worker. The worker runs continuously; the scheduler fires a coroutine at 7pm each day.

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

scheduler = AsyncIOScheduler()
scheduler.add_job(
    trigger_daily_call,          # async function that calls LiveKit API
    CronTrigger(hour=19, minute=0, timezone="America/Los_Angeles"),
)
scheduler.start()

# Then start the LiveKit agent worker
agents.cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, agent_name="accountability-buddy"))
```

**Pros:** Single process, no separate cron daemon, timezone-aware, built-in retry/coalesce logic.
**Cons:** Slightly more complex; scheduler shares the event loop with the agent worker.

Source: [APScheduler AsyncIOScheduler docs](https://apscheduler.readthedocs.io/en/3.x/modules/schedulers/asyncio.html)

---

### Option C: External cloud scheduler (best for reliability without 24/7 host)

If you do not want to run a persistent process, use a cloud scheduler that calls a webhook or HTTP endpoint to trigger the dispatch. Options:

| Service | Free tier | Notes |
|---------|-----------|-------|
| GitHub Actions (cron workflow) | Free | Calls a script via `workflow_dispatch` on schedule; runs on GitHub runners |
| fly.io Machines (scale-to-zero) | Small cost | Spin up container on schedule, dispatch call, container exits |
| Modal cron | Free tier | Python function scheduled as cron, no server needed |
| Google Cloud Scheduler + Cloud Run | Free tier | HTTP trigger, runs container on schedule |

**GitHub Actions approach** is the simplest free option for a personal project with an existing GitHub repo: define a workflow that runs `python trigger.py` on a schedule. The repo contains only the trigger script; the agent worker runs elsewhere.

---

### Recommendation

For this project: **APScheduler embedded in the agent process**. This keeps everything in one Docker container:
1. Agent worker listens for dispatch requests (LiveKit Cloud pushes jobs)
2. APScheduler fires daily at 7pm, calling `create_dispatch` + the agent picks it up as a job

No crontab dependency, no separate scheduler service, timezone-aware, single deployment unit.

---

## 4. Full End-to-End Orchestration Flow

```
19:00:00 — APScheduler fires trigger_daily_call()
    |
    ├─ Fetch habits from Habitify MCP (before call)
    |     → Get today's habits and completion status
    |
    ├─ Call lkapi.agent_dispatch.create_dispatch(
    |       agent_name="accountability-buddy",
    |       room="checkin-{date}",
    |       metadata={"phone_number": "+1...", "habits": [...]}
    |   )
    |     → LiveKit auto-creates room "checkin-{date}"
    |     → LiveKit dispatches job to agent worker
    |
    └─ Agent worker entrypoint() receives JobContext
          |
          ├─ Parse metadata (phone number + habits context)
          |
          ├─ ctx.api.sip.create_sip_participant(
          |       sip_trunk_id=SIP_OUTBOUND_TRUNK_ID,
          |       sip_call_to=phone_number,
          |       wait_until_answered=True
          |   )
          |     → Twilio SIP trunk dials the phone number
          |     → Phone rings, user answers
          |
          ├─ Set up AgentSession with OpenAI Realtime API
          |     → STT/TTS via OpenAI Realtime (WebRTC bridge)
          |     → LLM with habits context in system prompt
          |
          ├─ Conversation runs until:
          |     - User says goodbye / hangs up
          |     - All participants leave room
          |     - Explicit session.shutdown() call
          |
          ├─ Post-conversation: update habits via Habitify MCP
          |     → Mark completed habits, log notes
          |
          └─ session.shutdown(drain=True)
                → Graceful close, room deleted automatically
```

**Timing note:** `wait_until_answered=True` in `create_sip_participant` blocks the entrypoint coroutine until the phone is picked up (or `ringing_timeout` expires, max 80 seconds). If unanswered, the job ends cleanly and the room is discarded.

---

## 5. Environment Variables / Secrets

All variables required across the full system:

```bash
# LiveKit (self-hosted: set these; LiveKit Cloud: injected automatically)
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=APIxxxxxxxxxxxx
LIVEKIT_API_SECRET=your-secret

# OpenAI
OPENAI_API_KEY=sk-...

# Twilio SIP Trunk
SIP_OUTBOUND_TRUNK_ID=ST_xxxxxxxxxxxx   # from: lk sip outbound create

# Phone number to call (could be hardcoded for single-user project)
USER_PHONE_NUMBER=+15105550123

# Habitify (MCP server)
HABITIFY_API_KEY=your-habitify-key

# Optional: timezone for scheduler
TZ=America/Los_Angeles
```

**Twilio SIP trunk setup produces the `SIP_OUTBOUND_TRUNK_ID`.** Steps:
1. Create Twilio SIP trunk with domain `your-name.pstn.twilio.com`
2. Create credential list (username + password) in Twilio console
3. Associate credentials to trunk under Termination > Authentication
4. Register trunk with LiveKit: `lk sip outbound create outbound-trunk.json`
5. The CLI returns the trunk ID — store as `SIP_OUTBOUND_TRUNK_ID`

Source: [Twilio SIP trunk setup](https://docs.livekit.io/telephony/start/providers/twilio/), [outbound-caller-python .env.example](https://github.com/livekit-examples/outbound-caller-python)

---

## 6. Simplest Deployment for a Single-User Personal Project

**Recommended stack:**

```
LiveKit Cloud (free tier)
  └─ Handles: SIP, room management, agent dispatch, WebRTC

Single Docker container (run anywhere: local Mac, Fly.io $3/mo, Render free)
  ├─ agent.py (LiveKit worker — stays connected to LiveKit Cloud)
  └─ APScheduler (fires at 19:00, dispatches the agent to itself)
```

**The agent worker does not need to be reachable from the internet.** It opens an outbound WebSocket to LiveKit Cloud. This means you can run it on your Mac, a cheap VPS, or any container service — no ingress/load balancer needed.

**Fly.io deployment** is a pragmatic choice for "set it and forget it":
- `fly launch` from your project directory
- Set secrets via `fly secrets set OPENAI_API_KEY=...`
- ~$3/month for smallest machine (256MB RAM is sufficient for single concurrent call)
- Container stays running; APScheduler fires the daily job

**Alternatively, run it locally on your Mac** with `launchd` or just `python agent.py start` in a Terminal session. For a personal accountability tool, this may be all you need.

---

## 7. Agent Lifecycle: Startup, Conversation, Graceful Shutdown

### Worker Startup

```bash
python agent.py start   # production mode
python agent.py dev     # dev mode with auto-reload
```

On start, the worker:
1. Connects to LiveKit Cloud via WebSocket
2. Registers as worker named `"accountability-buddy"` (matching the `agent_name` in dispatch calls)
3. Enters standby, waiting for dispatch requests
4. APScheduler starts in background on the same event loop

### Startup Modes

| Mode | Command | Use case |
|------|---------|----------|
| `start` | `python agent.py start` | Production; graceful shutdown on SIGTERM |
| `dev` | `python agent.py dev` | Development; auto-reload on file change |
| `console` | `python agent.py console` | Local testing without LiveKit server |

There is no "one-shot" mode. The worker runs continuously and accepts multiple dispatch requests over its lifetime. This is correct for a scheduler-driven design — the worker stays up, and the scheduler fires the job.

### Conversation Lifecycle (per job)

Each dispatch creates an isolated subprocess:

```
Job received
  → entrypoint() called with JobContext
  → create_sip_participant() dials phone
  → wait_until_answered → phone answered
  → AgentSession starts (OpenAI Realtime)
  → Conversation runs
  → User hangs up OR session.shutdown(drain=True)
  → Post-conversation cleanup hook runs
  → subprocess exits
```

### Graceful Shutdown

```python
# End the conversation from agent code:
await session.shutdown(drain=True)
# drain=True: finishes speaking current sentence before closing
# non-blocking: returns immediately, shutdown happens in background
```

**On SIGTERM** (e.g., `docker stop` or `fly deploy`):
- Worker stops accepting new jobs
- Active jobs are allowed to complete
- `terminationGracePeriodSeconds` should be set to 10+ minutes in container orchestration config to avoid cutting off active calls

**Room cleanup:** Rooms close automatically when all non-agent participants leave (i.e., when the phone call ends). No manual room deletion needed.

Source: [Job lifecycle](https://docs.livekit.io/agents/server/job/), [Server startup modes](https://docs.livekit.io/agents/server/startup-modes/), [Server lifecycle](https://docs.livekit.io/agents/server/lifecycle/)

---

## Key Pitfalls

### 1. Automatic dispatch fires on every room creation
**Problem:** Without explicit dispatch configuration, the agent joins every room created in your LiveKit project — including test rooms.
**Fix:** Give the agent a name in code (`agent_name="accountability-buddy"`). Named agents only respond to explicit dispatch requests, not automatic ones.

### 2. SIP participant creation happens inside the agent, not outside
**Problem:** It is tempting to call `create_sip_participant` from the scheduler script before the agent is ready.
**Fix:** The scheduler dispatches the agent first. The agent's entrypoint reads the phone number from metadata and calls `create_sip_participant` after connecting to the room. The agent must be in the room before the SIP call arrives.

### 3. Ringing timeout is capped at 80 seconds
**Problem:** `ringing_timeout` in `CreateSIPParticipantRequest` maxes out at 80 seconds. If unanswered, the job ends.
**Fix:** Handle the case where the call is not answered — log it, optionally retry later via APScheduler.

### 4. LiveKit Cloud free tier has 1,000 agent minutes/month
**Problem:** Agent minutes are consumed by the worker process being connected, not just active calls.
**Fix:** Actually, agent minutes are billed per active session/job, not idle worker time. At ~15 min/call × 30 days = 450 minutes, the free tier easily covers daily calls.
**Note:** Verify this in billing docs — the exact metering model should be confirmed before going live.

### 5. `wait_until_answered=True` blocks the entrypoint coroutine
**Problem:** If you set up the AgentSession before the phone is answered, OpenAI Realtime is connected but the user is not yet there.
**Fix:** Call `create_sip_participant(wait_until_answered=True)` first, await it, then set up the AgentSession after it returns.

---

## Sources

- [LiveKit agent deployment overview](https://docs.livekit.io/deploy/agents/)
- [LiveKit agent deployment (get started)](https://docs.livekit.io/agents/ops/deployment/)
- [Self-hosted deployments](https://docs.livekit.io/deploy/custom/deployments/)
- [Agent dispatch documentation](https://docs.livekit.io/agents/server/agent-dispatch/)
- [Agent server startup modes](https://docs.livekit.io/agents/server/startup-modes/)
- [Job lifecycle](https://docs.livekit.io/agents/server/job/)
- [Server lifecycle](https://docs.livekit.io/agents/server/lifecycle/)
- [Outbound calls quickstart](https://docs.livekit.io/agents/quickstarts/outbound-calls/)
- [Outbound calls guide](https://docs.livekit.io/telephony/making-calls/outbound-calls/)
- [SIP outbound trunk](https://docs.livekit.io/telephony/making-calls/outbound-trunk/)
- [Twilio SIP trunk setup](https://docs.livekit.io/telephony/start/providers/twilio/)
- [SIP API reference](https://docs.livekit.io/reference/telephony/sip-api/)
- [Python SIP service API](https://docs.livekit.io/python/livekit/api/sip_service.html)
- [outbound-caller-python example](https://github.com/livekit-examples/outbound-caller-python)
- [agent-deployment examples](https://github.com/livekit-examples/agent-deployment)
- [LiveKit Cloud pricing](https://livekit.com/pricing)
- [APScheduler AsyncIOScheduler](https://apscheduler.readthedocs.io/en/3.x/modules/schedulers/asyncio.html)
- [Modal LiveKit deployment guide](https://modal.com/blog/livekit-modal)
