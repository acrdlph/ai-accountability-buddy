# Accountability Buddy

## What This Is

A voice-powered accountability system that calls you every evening to check in on your daily habits. It connects to your Habitify habit tracker via MCP, uses OpenAI Realtime for conversational AI, and calls your phone through Twilio — so you never have to manually track habits again. Built on LiveKit Agents as the orchestration layer.

## Core Value

The system eliminates manual habit tracking entirely — after a natural phone conversation, all habits are automatically marked complete or incomplete in Habitify without you ever opening the app.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] Cron-triggered daily call at a configurable evening time (default 7pm)
- [ ] LiveKit Agent (Python) orchestrates the entire flow
- [ ] Habitify MCP integration to read today's habits and their completion status
- [ ] Habitify MCP integration to mark habits as complete/incomplete after the call
- [ ] OpenAI Realtime API powers the voice conversation
- [ ] Twilio integration to make outbound phone calls
- [ ] Tough-love personality — direct, no-nonsense accountability
- [ ] Knows which habits are due today and which are already done before the call starts
- [ ] Natural conversation flow: greets, reviews habits, asks about each, motivates on incomplete ones
- [ ] Retry logic: if no answer, try again 30 minutes later (one retry max)
- [ ] Single-user setup with extensible design for future multi-user support

### Out of Scope

- Multi-user management UI — just me for now
- Web dashboard or admin panel — the phone call is the interface
- SMS/text-based check-ins — voice only for v1
- Custom scheduling (multiple times per day) — single evening call
- Integration with other habit trackers — Habitify only

## Context

- **Habitify** recently released MCP support, enabling programmatic access to read and write habit data
- **LiveKit Agents** is the framework for building the voice AI pipeline (Python SDK)
- **OpenAI Realtime API** provides the multimodal voice model for natural conversation
- **Twilio** handles telephony — making the actual outbound call and connecting audio
- The user currently tracks habits in Habitify but finds manual tracking tedious and often skips it
- This is a personal tool first, but should be architected cleanly enough to extend later

## Constraints

- **Voice Model**: OpenAI Realtime API — already decided
- **Orchestration**: LiveKit Agents (Python) — already decided
- **Telephony**: Twilio — already decided
- **Habit Data**: Habitify via MCP — already decided
- **Single User**: Personal phone number and Habitify account hardcoded for v1

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| LiveKit Agents as orchestrator | Purpose-built for voice AI pipelines, handles audio routing | — Pending |
| OpenAI Realtime for voice | Low-latency multimodal voice model, natural conversation | — Pending |
| Twilio for telephony | Industry standard for programmatic phone calls | — Pending |
| Habitify MCP for habit data | Native integration, no custom API work needed | — Pending |
| Tough-love personality | User preference — direct accountability, not gentle coaxing | — Pending |
| Retry once on no answer | Balance between persistence and annoyance, 30 min delay | — Pending |

---
*Last updated: 2026-03-10 after initialization*
