# Roadmap: Accountability Buddy

## Overview

Five phases take this project from a bootstrapped LiveKit agent (tested in a browser) to a fully deployed, self-running system that calls your phone every evening, reviews your habits in conversation, and writes the results back to Habitify automatically. Each phase builds cleanly on the last: get the voice agent talking first, then add telephony, then add habit data, then add autonomous scheduling, then harden for unattended production operation.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Core Voice Agent** - Working LiveKit + OpenAI Realtime agent with tough-love personality, testable in browser
- [ ] **Phase 2: Twilio SIP Telephony** - Agent calls a real phone number via Twilio Elastic SIP trunk
- [ ] **Phase 3: Habitify Integration** - Agent fetches today's habits and writes completion results back to Habitify
- [ ] **Phase 4: Scheduling and Retry** - System calls autonomously at 7pm daily and retries once on no-answer
- [ ] **Phase 5: Deployment and Hardening** - Single Docker container running on Fly.io with structured logging and auto-restart

## Phase Details

### Phase 1: Core Voice Agent
**Goal**: A working voice agent that holds a natural conversation with a tough-love accountability personality, verifiable without making any phone calls
**Depends on**: Nothing (first phase)
**Requirements**: FR3, FR5, FR6, FR9, NFR1, NFR2
**Success Criteria** (what must be TRUE):
  1. Running `python agent.py dev` starts the agent worker and it connects to LiveKit Cloud without errors
  2. Opening the LiveKit browser playground and joining the room causes the agent to speak first within 2 seconds — no waiting, no silence
  3. The agent's tone is direct and no-nonsense: it does not hedge, apologize, or soften its assessments
  4. After the conversation ends naturally, the agent says goodbye and the room is deleted cleanly — no lingering connections
  5. Voice responses feel natural with no perceptible lag (OpenAI Realtime single-hop speech-to-speech)
**Plans**: TBD

Plans:
- [ ] 01-01: Bootstrap from outbound-caller-python template, configure environment, verify worker connects to LiveKit Cloud
- [ ] 01-02: Implement AccountabilityAgent with tough-love system prompt, on_enter generate_reply, end_call tool, BVCTelephony noise cancellation
- [ ] 01-03: Browser test — verify agent speaks first, conversation flows naturally, room deletion terminates session cleanly

### Phase 2: Twilio SIP Telephony
**Goal**: The agent can call a real phone number — a manual CLI dispatch rings a phone and the agent speaks
**Depends on**: Phase 1
**Requirements**: FR1, FR8, NFR4
**Success Criteria** (what must be TRUE):
  1. Running `lk dispatch create --new-room --agent-name accountability-buddy --metadata '{"phone":"+1..."}'` rings the target phone within 10 seconds
  2. Answering the call connects immediately to the agent, which speaks first
  3. If the call goes unanswered, the agent process exits cleanly (TwirpError caught, no crash)
  4. If voicemail picks up, the agent detects the greeting and hangs up without leaving a message
  5. Single hardcoded phone number is configurable via environment variable (extensible for future multi-user)
**Plans**: TBD

Plans:
- [ ] 02-01: Configure Twilio Elastic SIP trunk with credential-list auth, register LiveKit outbound SIP trunk, set SIP_OUTBOUND_TRUNK_ID env var
- [ ] 02-02: Implement create_sip_participant() in agent entrypoint with wait_until_answered=True and TwirpError handling for no-answer; implement detected_answering_machine tool
- [ ] 02-03: End-to-end test — manual CLI dispatch rings phone, agent speaks first, no-answer path exits cleanly, voicemail path hangs up

### Phase 3: Habitify Integration
**Goal**: The agent knows which habits are due today before the call starts and writes accurate completion results to Habitify during the conversation
**Depends on**: Phase 2
**Requirements**: FR2, FR4
**Success Criteria** (what must be TRUE):
  1. At call start, the agent correctly names each habit due today and knows which are already marked complete
  2. When the user says a habit is done, Habitify reflects completed status immediately after the call
  3. For goal-based habits (e.g., "ran 5km"), Habitify logs the numeric value — not just a yes/no status
  4. Habits the user says they skipped are correctly left as incomplete in Habitify
  5. The agent handles at least 5 habits in a single call without tool-step limits blocking completion
**Plans**: TBD

Plans:
- [ ] 03-01: Implement Habitify REST client — GET /journal at dispatch time, pass habit list via dispatch metadata; validate both simple and goal-based habit types against real Habitify account
- [ ] 03-02: Implement log_habit function tool with branching: PUT /status/:id for simple habits, POST /logs/:id for goal-based habits; set max_tool_steps to habits + 2
- [ ] 03-03: End-to-end test — live call with real Habitify data, verify pre-call habit fetch, verify all habit types write correctly after conversation

### Phase 4: Scheduling and Retry
**Goal**: The system calls autonomously at 7pm every day without any manual trigger, and retries once 30 minutes later if unanswered
**Depends on**: Phase 3
**Requirements**: FR7
**Success Criteria** (what must be TRUE):
  1. At 7pm local time, the phone rings without any manual action — no CLI command, no terminal open
  2. If the 7pm call goes unanswered, the phone rings again at exactly 7:30pm
  3. No third attempt is made — the system logs the miss and waits until tomorrow
  4. If the 7pm call is answered, no retry fires at 7:30pm
  5. Timezone is configurable via environment variable; scheduler does not drift across days
**Plans**: TBD

Plans:
- [ ] 04-01: Embed APScheduler AsyncIOScheduler in the agent process with CronTrigger(hour=19, timezone=env) calling trigger_daily_call()
- [ ] 04-02: Implement retry logic — agent writes outcome metadata (answered/no_answer/voicemail) to room before shutdown; scheduler reads outcome and dispatches retry if no_answer with attempt count guard
- [ ] 04-03: Validation — run scheduler for two consecutive trigger windows, confirm correct retry behavior on no-answer and no retry on answer

### Phase 5: Deployment and Hardening
**Goal**: A single Docker container runs on Fly.io unattended, calls daily, restarts automatically on crash, and produces logs for post-hoc review
**Depends on**: Phase 4
**Requirements**: NFR3, NFR5
**Success Criteria** (what must be TRUE):
  1. `docker build` produces a working image; `fly deploy` pushes it to Fly.io without errors
  2. The system makes its daily call from Fly.io without any local machine running
  3. If the container crashes mid-session, Fly.io restarts it automatically and the next day's call proceeds normally
  4. Each call produces a structured log entry with timestamp, habits discussed, and call outcome (answered/voicemail/no-answer)
  5. Total monthly cost stays within LiveKit Cloud free tier plus minimal Twilio and OpenAI charges
**Plans**: TBD

Plans:
- [ ] 05-01: Write Dockerfile (Python 3.11-slim, uv, single entrypoint); configure fly.toml with terminationGracePeriodSeconds=900 and auto-restart; set all secrets via fly secrets set
- [ ] 05-02: Add structured JSON logging to stdout (timestamp, call outcome, habit results); optionally write per-call summary to SQLite for review
- [ ] 05-03: Production validation — deploy to Fly.io, confirm autonomous daily call fires, confirm auto-restart on simulated crash, verify logs are queryable

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Core Voice Agent | 0/3 | Not started | - |
| 2. Twilio SIP Telephony | 0/3 | Not started | - |
| 3. Habitify Integration | 0/3 | Not started | - |
| 4. Scheduling and Retry | 0/3 | Not started | - |
| 5. Deployment and Hardening | 0/3 | Not started | - |
