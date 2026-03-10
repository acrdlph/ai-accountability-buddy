# Project Research Summary

**Project:** Accountability Buddy — voice AI agent for daily habit check-ins
**Domain:** Scheduled outbound telephony with voice AI and habit tracking integration
**Researched:** 2026-03-10
**Confidence:** HIGH (primary sources across all four research areas are official LiveKit docs and official example repos)

---

## Executive Summary

Accountability Buddy is a scheduled outbound voice AI agent: at 7pm each day, it calls the user's phone, conducts a conversational habit check-in, and records the results back to Habitify. The research confirms that the full stack (LiveKit Agents + OpenAI Realtime + Twilio SIP + Habitify) is production-capable and well-documented. Crucially, LiveKit maintains an official `outbound-caller-python` example repo that matches this project's exact stack — it should be the starting template, not a from-scratch implementation.

The core architecture is validated and well-understood: an APScheduler fires daily at 7pm inside the same Python process as the LiveKit agent worker, dispatches an agent job via the LiveKit API, and the agent's entrypoint creates the Twilio SIP participant to place the actual call. OpenAI Realtime API handles speech-in and speech-out in a single model hop (no separate STT/TTS), and Habitify habit data is fetched and written via their REST API using a simple API key. Everything runs in a single Docker container, deployable to LiveKit Cloud or any VPS for under $5/month.

The one significant risk is Habitify integration: the official Habitify MCP server uses OAuth designed for interactive browser flows, which is problematic for a headless server process. The recommended mitigation is to bypass MCP entirely and use Habitify's REST API directly as `@function_tool` decorated functions — this is simpler, fully documented, and avoids OAuth complexity. The other non-trivial risk is the ordering constraint between agent connection and SIP dialing: the agent must be connected to the LiveKit room before `create_sip_participant()` is called, and the SIP participant must be answered before the agent session starts. Getting this sequence wrong produces silent calls or calls to a dial tone.

---

## Key Findings

### Recommended Stack

The LiveKit Agents 1.0 framework (Python, released April 2025) provides a two-tier architecture: a long-lived worker process registers with LiveKit Cloud, and each dispatched job spawns an isolated child subprocess. The `AgentSession` with `openai.realtime.RealtimeModel` eliminates the need for separate STT and TTS — OpenAI Realtime API handles the full speech-to-speech loop in one hop, reducing latency. For telephony, `noise_cancellation.BVCTelephony()` must be used instead of the generic BVC plugin, as it is tuned for PSTN audio characteristics. The framework uses `uv` as the package manager and Python 3.11+.

**Core technologies:**
- **LiveKit Agents 1.0 (Python):** Orchestration framework — official, well-documented, has exact outbound-caller example
- **OpenAI Realtime API:** Voice AI — single-hop speech-to-speech, lower latency than STT+LLM+TTS pipeline
- **Twilio Elastic SIP Trunking:** PSTN connectivity — use username/password auth (not IP allowlist; LiveKit IPs are not static)
- **Habitify REST API:** Habit data — simple API key auth, well-documented endpoints
- **APScheduler (AsyncIOScheduler):** Scheduling — timezone-aware, embedded in the same process, no external cron dependency
- **LiveKit Cloud (free tier):** Hosting SIP bridge, room management, agent dispatch — 1,000 free agent minutes/month covers ~450 min/month at 15 min/call

### Core Capabilities Required

**Must have (required for the product to work at all):**
- Scheduled outbound call at 7pm via Twilio SIP trunk
- Agent speaks first on connection (via `on_enter` / `generate_reply()`)
- Habit list fetched from Habitify before or at call start
- Per-habit completion logging during conversation
- Voicemail detection (LLM-based) to hang up and not leave messages
- No-answer handling: catch `TwirpError`, log the miss
- Single retry attempt 30 minutes after no-answer
- Graceful call termination (agent says goodbye, then deletes the room)

**Should have (quality of life):**
- Noise cancellation (`BVCTelephony`) for PSTN audio quality
- Outcome metadata written to room before shutdown (enables external retry detection)
- Per-call logging with timestamps, habit results, and call outcome
- System prompt tuned for phone conversation (no markdown, concise responses)

**Defer to v2+:**
- Inbound call support (user calling in to check their own stats)
- Multiple users / multi-tenant
- Web dashboard for habit review
- SMS follow-up after missed calls
- Custom voice via Cartesia (requires switching Realtime to text-only mode + separate TTS)

### Architecture Approach

The system is a single Python process containing both the LiveKit agent worker and an embedded APScheduler. The scheduler dispatches the agent at 7pm; the agent's entrypoint handles SIP dialing, conversation, and Habitify writes. All Habitify interaction happens via the REST API as `@function_tool` decorated functions on the `AccountabilityAgent` class — there is no MCP server in the loop. Call outcomes (answered/no-answer/voicemail) are communicated from the agent subprocess back to the scheduler via room metadata written before shutdown.

**Major components:**
1. **APScheduler** — fires `trigger_daily_call()` at 19:00 in the configured timezone; calls `lkapi.agent_dispatch.create_dispatch()`; handles retry scheduling on no-answer
2. **LiveKit agent worker** — long-lived process registered as `"accountability-buddy"`; receives dispatched jobs; each job runs in an isolated subprocess
3. **AccountabilityAgent** — `Agent` subclass with system prompt, `log_habit` tool, `detected_answering_machine` tool, and `end_call` tool; calls `generate_reply()` on `on_enter` to speak first
4. **Habitify REST client** — called inside function tools; fetches journal at call start (passed via dispatch metadata), writes completions during conversation
5. **SIP/Twilio bridge** — LiveKit SIP service bridges the room to Twilio Elastic SIP Trunk → PSTN; audio codec: PCMU (8kHz standard telephone quality)

**Critical ordering constraint (violating this breaks everything):**
```
1. create_dispatch()          ← from scheduler
2. agent connects to room     ← ctx.connect() or session.start()
3. create_sip_participant()   ← from inside entrypoint, AFTER agent is in room
4. wait_until_answered=True   ← blocks until answered
5. session.start() / generate_reply()  ← AFTER call is answered
```

### Critical Pitfalls

1. **Agent must be in the room before dialing** — `create_sip_participant()` must be called from inside the agent's entrypoint, after `ctx.connect()`. Calling it from the scheduler before the agent joins the room means the SIP participant arrives with no agent to handle their audio. This is the most common implementation mistake.

2. **Agent must speak first on outbound calls** — implement `on_enter(self)` to call `await self.session.generate_reply()`. Without this, the call connects to silence; the user hangs up confused.

3. **`wait_until_answered=True` is required for correct ordering** — without it, the agent session may begin before the call is answered, causing the agent to speak to a dial tone (or Twilio's ring-back audio). Set `ringing_timeout` to 30–45 seconds; the cap is 80 seconds.

4. **Use `TwirpError` for no-answer detection, not `disconnect_reason`** — Python SDK `participant.disconnect_reason` is documented but has known reliability issues (issue #398, not fixed). `TwirpError` raised by `create_sip_participant` when `wait_until_answered=True` times out is the reliable signal.

5. **Voicemail detection is LLM-based, not carrier-based** — LiveKit SIP has no AMD (answering machine detection). Give the agent a `detected_answering_machine` tool; the LLM recognizes voicemail greetings and calls it. This works in practice but could miss unusual greetings. Setting `ringing_timeout=45s` (not 30s) gives voicemail time to pick up before hanging up if detection is desired.

6. **Habitify has two completion APIs** — simple habits use `PUT /status/:id` with `"status": "completed"`; goal-based habits (e.g., "run 5km") must use `POST /logs/:id` with a numeric value. The status endpoint silently rejects `completed` for goal-tracked habits. The agent's `log_habit` tool must detect the habit type and route accordingly.

7. **Use credential-list auth for Twilio, not IP allowlisting** — LiveKit Cloud nodes have no static IPs; IP-based auth produces 403 errors. Always configure a username/password credential list on the Twilio elastic trunk.

8. **Deleting the room hangs up the call; closing the session does not** — `ctx.api.room.delete_room()` disconnects all SIP participants. Calling only `session.aclose()` leaves the phone line open.

---

## Implications for Roadmap

### Phase 1: Core Voice Agent (LiveKit + OpenAI Realtime, local test)

**Rationale:** Everything else depends on a working agent. This phase validates the LiveKit/OpenAI integration in isolation before adding telephony complexity. Bootstrap from `outbound-caller-python` template — it is the exact stack and removes boilerplate.

**Delivers:** A running agent that can hold a voice conversation. Testable in `dev` mode via LiveKit's browser playground without any phone calls.

**Implements:**
- `AccountabilityAgent` class with system prompt tuned for phone conversation
- `on_enter` → `generate_reply()` for agent-speaks-first pattern
- `end_call` tool (room deletion)
- `detected_answering_machine` tool (stub)
- Noise cancellation with `BVCTelephony()`
- `.env` with `LIVEKIT_*` and `OPENAI_API_KEY`

**Avoids:** Tool speech coordination race (use `context.wait_for_playout()` not `generate_reply()` inside tools from the start)

**Research flag:** None — well-documented, official example available.

---

### Phase 2: Twilio SIP Telephony Integration

**Rationale:** Telephony must be proven before adding habit logic. SIP trunk configuration is the most failure-prone external setup step (credentials, domain format, static IP issue). Validate with a single test call before building on top.

**Delivers:** The agent calls a real phone number. A test dispatch via the `lk dispatch create` CLI rings a phone and the agent speaks.

**Implements:**
- Twilio Elastic SIP Trunk creation and credential list configuration
- LiveKit outbound trunk registration (`lk sip outbound create`)
- `create_sip_participant()` in the agent entrypoint with `wait_until_answered=True`
- `TwirpError` exception handling for no-answer
- `SIP_OUTBOUND_TRUNK_ID` env var
- Manual CLI dispatch for testing (`lk dispatch create --new-room --agent-name ...`)

**Avoids:** IP allowlisting (use credential list); dialing before agent joins room; wrong Twilio domain format (`<name>.pstn.twilio.com` not `sip:...`)

**Research flag:** None — official Twilio setup guide exists; `outbound-caller-python` example covers this exactly.

---

### Phase 3: Habitify Integration

**Rationale:** Habit data is the content of the conversation. This phase wires the agent to real Habitify data so it knows what to ask about and can write results back.

**Delivers:** The agent fetches today's habits from Habitify, asks about each one, and marks them complete/incomplete in Habitify during the call.

**Implements:**
- `HABITIFY_API_KEY` env var (from Habitify app Settings → Account → API Credential)
- `GET /journal` call at dispatch time to get today's habit list; pass via dispatch `metadata`
- `log_habit` function tool with branching logic:
  - Simple habits: `PUT /status/:id` with `"status": "completed"`
  - Goal-based habits: `POST /logs/:id` with numeric value
- `max_tool_steps` set to at least (number of habits + 2) to allow sequential logging

**Avoids:** Official MCP server OAuth complexity — use REST API directly; goal vs. no-goal API routing confusion

**Research flag:** Needs empirical validation. The branching between the two completion APIs is a documented gotcha that must be tested with actual Habitify habit types. The community MCP server is an alternative if direct REST feels clunky, but Node.js runtime dependency in the container must be accounted for.

---

### Phase 4: Scheduling and No-Answer Retry

**Rationale:** The product is only useful if it calls reliably every day. This phase adds the scheduler and retry logic, and is the last major integration piece.

**Delivers:** The agent calls autonomously at 7pm daily without any manual trigger. If unanswered, retries once 30 minutes later.

**Implements:**
- APScheduler `AsyncIOScheduler` embedded in the agent process
- `CronTrigger(hour=19, minute=0, timezone="America/Los_Angeles")`
- `trigger_daily_call()` async function calling `lkapi.agent_dispatch.create_dispatch()`
- Agent writes outcome metadata to room participant before shutdown (`no_answer`, `answered`, `voicemail`)
- Retry logic: if `TwirpError` → wait 30 min → dispatch again (with attempt count in metadata to prevent infinite loops)

**Avoids:** Scheduler firing dispatch before agent worker is registered (ensure worker starts before scheduler begins); scheduling in UTC vs. local time mismatch

**Research flag:** None — APScheduler patterns are standard; retry orchestration via room metadata is a LiveKit-documented pattern.

---

### Phase 5: Deployment and Hardening

**Rationale:** The system needs to run reliably without babysitting. This phase packages everything for unattended operation.

**Delivers:** A single Docker container running on Fly.io (or local Mac via launchd) that handles daily calls without manual intervention.

**Implements:**
- `Dockerfile` (Python 3.11-slim, `uv` for deps)
- `fly.toml` with `terminationGracePeriodSeconds` set to 900 (15 min) to avoid mid-call SIGTERM
- Secrets set via `fly secrets set`
- Logging to stdout (structured JSON preferred)
- Call outcome logging to a file or simple SQLite for post-hoc review

**Avoids:** LiveKit Cloud injecting `LIVEKIT_*` env vars automatically (don't set them in `fly.toml` if using LiveKit Cloud agent deployment); free tier metering misunderstanding (confirm: billed per active job, not idle worker time)

**Research flag:** Confirm LiveKit Cloud free tier billing model (per active job vs. per connected worker) before committing to that hosting path. This is flagged as "should verify" in the scheduling research.

---

### Phase Ordering Rationale

- **Phase 1 before Phase 2:** The agent must work in isolation (browser test) before adding telephony. Debugging a broken conversation over a phone call is much harder than debugging it in a browser.
- **Phase 2 before Phase 3:** SIP telephony is more failure-prone than the Habitify API. Validate the call infrastructure first. A working phone call with no habit data is more useful than habit data with no phone call.
- **Phase 3 before Phase 4:** Scheduling a broken Habitify integration just means daily broken calls. Get the conversation right first.
- **Phase 4 before Phase 5:** Deploy something that works end-to-end. Packaging a non-working system just hardens bugs.

### Research Flags

Phases that need deeper investigation during or before planning:
- **Phase 3 (Habitify):** Empirically validate the two-API completion branching. Test with at least one simple habit and one goal-based habit before writing the production tool. Also validate whether `skipped` status is available on the account (pre-Aug 2020 only).
- **Phase 5 (Deployment):** Confirm LiveKit Cloud free tier metering model. If billed per connected worker (not per active job), a 1,000 min/month limit could be consumed by the idle worker, not just the calls.

Phases with well-documented patterns (can proceed without additional research):
- **Phase 1:** Official LiveKit docs + `outbound-caller-python` example cover this completely.
- **Phase 2:** Official Twilio/LiveKit SIP setup guide is authoritative; `outbound-caller-python` is the exact reference.
- **Phase 4:** APScheduler is mature and well-documented; dispatch pattern is standard.

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| LiveKit + OpenAI Realtime integration | HIGH | Primary sources: official docs, official example repo. Complete working code examples exist for the exact stack. |
| Twilio SIP telephony | HIGH | Primary sources: official LiveKit/Twilio setup guide, `outbound-caller-python` example. One known SDK bug (disconnect_reason) is documented and has a reliable workaround. |
| Habitify REST API | HIGH | Official API docs are complete. Endpoint behavior for both completion paths is documented. |
| Habitify MCP (official) | LOW | OAuth headless flow is undocumented. Not recommended for this project. |
| Scheduling (APScheduler) | HIGH | Mature library, standard patterns, LiveKit dispatch API is documented. |
| Deployment cost (LiveKit free tier) | MEDIUM | Billing model description is slightly ambiguous — "per active session/job" needs confirmation. |

**Overall confidence:** HIGH — with the one caveat that Habitify API behavior for goal-based habit completion should be validated empirically before relying on it.

### Gaps to Address

- **Habitify goal-vs-simple habit routing:** Must test both `PUT /status/:id` and `POST /logs/:id` against a real Habitify account with both habit types. The distinction is critical; the agent tool must handle both. Discover during Phase 3.

- **Voicemail detection reliability:** LLM-based detection using the `detected_answering_machine` tool works in the documented examples but is not carrier-verified. Unusual voicemail greetings (non-English, custom recordings) may confuse the LLM. Acceptable risk for a personal-use tool; revisit if false positives appear in use.

- **LiveKit Cloud billing model confirmation:** The research notes that free tier is "1,000 agent session minutes/month" and believes this is per active job, not idle worker time. Confirm in the LiveKit Cloud billing dashboard before assuming the free tier is sufficient. If billed per connected worker, self-hosting the worker on Fly.io is preferable.

- **Retry outcome detection:** The simplest retry mechanism (write outcome to room metadata before shutdown, then read from scheduler) requires the scheduler to poll or use LiveKit webhooks to detect job completion. This handshake between the scheduler and agent subprocess needs an implementation decision. Webhooks are more robust; polling is simpler. Decide in Phase 4.

---

## Sources

### Primary (HIGH confidence — official docs and official example repos)

- [LiveKit Agents Introduction](https://docs.livekit.io/agents/) — agent architecture, session lifecycle, dispatch
- [OpenAI Realtime Plugin Guide](https://docs.livekit.io/agents/models/realtime/plugins/openai/) — `RealtimeModel` configuration
- [Outbound Calls Quickstart](https://docs.livekit.io/agents/quickstarts/outbound-calls/) — dispatch + SIP participant pattern
- [Twilio SIP Trunk Setup for LiveKit](https://docs.livekit.io/telephony/start/providers/twilio/) — step-by-step trunk configuration
- [outbound-caller-python (official example)](https://github.com/livekit-examples/outbound-caller-python) — primary implementation reference
- [agent-starter-python (official example)](https://github.com/livekit-examples/agent-starter-python) — project structure reference
- [Habitify REST API Docs](https://docs.habitify.me/) — journal, status, logs endpoints
- [Habitify MCP API Docs](http://api-docs.habitify.me/mcp/others/) — MCP transport and tool categories
- [LiveKit agent deployment](https://docs.livekit.io/deploy/agents/) — deployment options and free tier
- [APScheduler AsyncIOScheduler docs](https://apscheduler.readthedocs.io/en/3.x/modules/schedulers/asyncio.html) — scheduler embedding

### Secondary (MEDIUM confidence)

- [tabtablabs: LiveKit + OpenAI Realtime tutorial](https://tabtablabs.com/blog/livekit-openai-realtime-voice-agent) — third-party walkthrough confirming patterns
- [AssemblyAI MCP Voice Agent Tutorial](https://www.assemblyai.com/blog/mcp-voice-agent-openai-livekit) — MCP + LiveKit integration pattern
- [Community Habitify MCP server](https://github.com/sargonpiraev/habitify-mcp-server) — fallback option if direct REST is insufficient
- [LiveKit Python SDK disconnect_reason issue #398](https://github.com/livekit/python-sdks/issues/398) — known SDK bug, informs workaround

---

*Research completed: 2026-03-10*
*Ready for roadmap: yes*
