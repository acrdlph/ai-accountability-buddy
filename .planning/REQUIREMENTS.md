# Requirements — Accountability Buddy

## Problem Statement

Manual habit tracking is tedious and easy to skip. The user tracks habits in Habitify but wants a system that automatically checks in and logs completion through a natural voice conversation — eliminating the need to ever open the app.

## Solution

A voice AI agent that calls the user every evening, reviews their daily habits, asks about completion, provides tough-love motivation, and automatically marks habits as complete/incomplete in Habitify.

---

## Functional Requirements

### FR1: Daily Outbound Call
The system calls the user's phone at a configurable evening time (default 7pm) using Twilio SIP telephony via LiveKit.
- **Acceptance:** Phone rings at configured time; answering connects to the voice agent.

### FR2: Habit Awareness
Before the call, the agent fetches today's habits and their current completion status from Habitify REST API.
- **Acceptance:** Agent knows which habits are due today and which (if any) are already marked complete.

### FR3: Conversational Check-In
The agent conducts a natural voice conversation reviewing each habit, asking whether the user completed it, and providing motivation for incomplete habits.
- **Acceptance:** Agent discusses each due habit by name, confirms completion status through conversation.

### FR4: Automatic Habit Tracking
Based on the conversation, the agent marks habits as complete or logs progress in Habitify — handling both simple (yes/no) and goal-based (numeric) habits.
- **Acceptance:** After the call, Habitify reflects the correct completion status for all discussed habits.

### FR5: Tough-Love Personality
The agent has a direct, no-nonsense personality. It acknowledges completed habits briefly and challenges the user on incomplete ones.
- **Acceptance:** Agent tone is motivating but firm — not passive or overly supportive.

### FR6: Agent Speaks First
On call connection, the agent initiates the conversation (the user shouldn't hear silence).
- **Acceptance:** Within 1-2 seconds of answering, the agent greets the user.

### FR7: No-Answer Retry
If the user doesn't answer, the system retries once after 30 minutes. Maximum 2 attempts per day.
- **Acceptance:** Missed call at 7pm triggers a retry at 7:30pm. No further attempts after that.

### FR8: Voicemail Detection
If a voicemail system answers instead of the user, the agent detects it and hangs up (no message left).
- **Acceptance:** Agent recognizes voicemail greetings and disconnects gracefully.

### FR9: Graceful Call Termination
After reviewing all habits, the agent wraps up the conversation naturally and hangs up by deleting the LiveKit room.
- **Acceptance:** Call ends cleanly — no lingering connections or zombie rooms.

---

## Non-Functional Requirements

### NFR1: Latency
Voice responses should feel natural — sub-second response time enabled by OpenAI Realtime's single-hop speech-to-speech model.

### NFR2: Audio Quality
Telephony-optimized noise cancellation (LiveKit BVCTelephony) for clean PSTN audio.

### NFR3: Reliability
System runs unattended daily. If the agent process crashes, it should restart automatically (container orchestration).

### NFR4: Single User
Hardcoded for one user's phone number and Habitify account. Clean code structure to allow multi-user extension later.

### NFR5: Cost
Stay within LiveKit Cloud free tier (~1,000 agent min/month). Twilio and OpenAI costs should be minimal for one daily call.

---

## Technical Constraints

| Constraint | Value | Rationale |
|------------|-------|-----------|
| Language | Python 3.11+ | LiveKit Agents SDK requirement |
| Package manager | uv | LiveKit ecosystem standard |
| Voice model | OpenAI Realtime API | User-specified; single-hop voice |
| Orchestration | LiveKit Agents 1.0 | User-specified; official outbound-caller template |
| Telephony | Twilio Elastic SIP Trunk | User-specified; credential-list auth (not IP) |
| Habit data | Habitify REST API | Simpler than MCP OAuth; API key auth |
| Scheduling | APScheduler (embedded) | No external cron dependency |
| Deployment | LiveKit Cloud | Free tier (1,000 agent min/month), zero ops |

---

## Out of Scope (v1)

- Multi-user / multi-tenant support
- Web dashboard or admin panel
- SMS/text fallback for missed calls
- Inbound call support (user calling the agent)
- Custom voice (Cartesia TTS)
- Integration with other habit trackers
- Multiple daily check-in times

---

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| FR1 | Phase 2 — Twilio SIP Telephony | Pending |
| FR2 | Phase 3 — Habitify Integration | Pending |
| FR3 | Phase 1 — Core Voice Agent | Pending |
| FR4 | Phase 3 — Habitify Integration | Pending |
| FR5 | Phase 1 — Core Voice Agent | Pending |
| FR6 | Phase 1 — Core Voice Agent | Pending |
| FR7 | Phase 4 — Scheduling and Retry | Pending |
| FR8 | Phase 2 — Twilio SIP Telephony | Pending |
| FR9 | Phase 1 — Core Voice Agent | Pending |
| NFR1 | Phase 1 — Core Voice Agent | Pending |
| NFR2 | Phase 1 — Core Voice Agent | Pending |
| NFR3 | Phase 5 — Deployment and Hardening | Pending |
| NFR4 | Phase 2 — Twilio SIP Telephony | Pending |
| NFR5 | Phase 5 — Deployment and Hardening | Pending |

---

*Derived from: PROJECT.md + research synthesis (2026-03-10)*
