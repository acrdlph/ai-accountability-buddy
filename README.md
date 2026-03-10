# Accountability Buddy

An AI voice agent that calls you every evening to review your habits. It pulls your real habit data from [Habitify](https://www.habitify.me/), has a natural voice conversation about what you did and didn't do, and logs completions back automatically.

Built with [LiveKit Agents](https://docs.livekit.io/agents/), [OpenAI Realtime API](https://platform.openai.com/docs/guides/realtime), and [Habitify MCP](https://www.habitify.me/).

> **Work in progress.** The core voice agent and Habitify integration are functional. However, the model still misunderstands and makes tracking mistakes easily - this will require some more prompt engineering. Scheduling and production deployment are still ahead. See the [Roadmap](#roadmap) for details.

## How It Works

The agent runs in two stages:

1. **Pre-call briefing** — A reasoning agent connects to Habitify via [Model Context Protocol (MCP)](https://modelcontextprotocol.io/), fetches your habits for the last 3–5 days, and builds a briefing with patterns, streaks, and talking points.

2. **Voice call** — The agent dials your phone via Twilio SIP, greets you with the briefing context, walks through each habit, and logs completions in real-time as you confirm them. If it hits voicemail, it hangs up.

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────┐
│  Habitify MCP    │────▶│  Briefing Agent  │────▶│  Voice Agent │
│  (habit data)    │     │  (gpt-5-mini)    │     │  (Realtime)  │
└──────────────────┘     └──────────────────┘     └──────┬───────┘
                                                         │
                              ┌──────────────────┐       │
                              │  Twilio SIP      │◀──────┘
                              │  (phone call)    │
                              └──────────────────┘
```

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- A [LiveKit Cloud](https://cloud.livekit.io/) account (free tier works)
- An [OpenAI API](https://platform.openai.com/) key
- A [Twilio](https://www.twilio.com/) account with a purchased phone number and an Elastic SIP Trunk (see [Twilio setup](#set-up-twilio-sip) below)
- A [Habitify](https://www.habitify.me/) account with habits set up

## Setup

### 1. Install dependencies

```bash
uv sync
```

### 2. Set up Twilio SIP

The agent makes outbound phone calls through Twilio. You'll need to:

1. **Buy a phone number** — In the Twilio Console, go to *Phone Numbers > Manage > Buy a Number* and purchase one with voice capability.
2. **Create an Elastic SIP Trunk** — Go to *Elastic SIP Trunking > Trunks > Create new SIP Trunk*. Give it a name (e.g. "accountability-buddy").
3. **Add a Credential List** — Under your trunk's *Authentication* tab, create a credential list. LiveKit uses these credentials to authenticate outbound calls.
4. **Register the trunk in LiveKit** — In the LiveKit Cloud dashboard, create an outbound SIP trunk pointed at your Twilio trunk. This gives you the `SIP_OUTBOUND_TRUNK_ID` for your `.env.local`.

For detailed instructions, see the [LiveKit SIP Quickstart](https://docs.livekit.io/agents/quickstarts/outbound-calls/) and [Twilio Elastic SIP Trunking docs](https://www.twilio.com/docs/sip-trunking).

### 3. Connect Habitify

Run the one-time OAuth setup. It registers an OAuth client, opens your browser to authorize, and saves credentials to `.env.local`:

```bash
uv run scripts/habitify_oauth_setup.py
```

### 4. Configure environment

Create `.env.local` in the project root (the OAuth setup script will have already created this file with Habitify credentials):

```bash
# LiveKit
LIVEKIT_URL=wss://your-instance.livekit.cloud
LIVEKIT_API_KEY=your-key
LIVEKIT_API_SECRET=your-secret

# OpenAI
OPENAI_API_KEY=sk-proj-...

# Twilio SIP
SIP_OUTBOUND_TRUNK_ID=ST_...
DEFAULT_PHONE_NUMBER=+1234567890

# Habitify (added by OAuth setup script)
HABITIFY_CLIENT_ID=...
HABITIFY_REFRESH_TOKEN=...
```

### 5. Run the agent

```bash
uv run agent.py dev
```

This starts a LiveKit Agents worker in development mode. Dispatch a call from the [LiveKit Cloud dashboard](https://cloud.livekit.io/) or via the LiveKit API.

## Project Structure

```
agent.py                 # Voice agent — entrypoint, SIP dialing, conversation
habitify_briefing.py     # Pre-call reasoning agent — MCP tool-calling loop
habitify_auth.py         # OAuth token refresh
scripts/
  habitify_oauth_setup.py  # One-time OAuth setup (PKCE flow)
```

## Tech Stack

| Component | Technology |
|-----------|------------|
| Voice conversation | [OpenAI Realtime API](https://platform.openai.com/docs/guides/realtime) |
| Agent framework | [LiveKit Agents v1.4](https://docs.livekit.io/agents/) |
| Habit data | [Habitify MCP Server](https://www.habitify.me/) |
| Phone calls | [Twilio Elastic SIP Trunking](https://www.twilio.com/docs/sip-trunking) |
| Pre-call analysis | [OpenAI Responses API](https://platform.openai.com/docs/api-reference/responses) (gpt-5-mini) |
| Noise cancellation | [LiveKit BVC Telephony](https://docs.livekit.io/agents/plugins/noise-cancellation/) |

## Roadmap

| Phase | Status |
|-------|--------|
| 1. Core Voice Agent | Done |
| 2. Twilio SIP Telephony | Done |
| 3. Habitify Integration | In Progress |
| 4. Scheduling & Retry (7pm daily, auto-retry) | Not Started |
| 5. Deployment & Hardening (LiveKit Cloud) | Not Started |

See [`.planning/ROADMAP.md`](.planning/ROADMAP.md) for the full breakdown with success criteria and plan details.

## Built With GSD

This project was planned and built using [GSD (Get Shit Done)](https://github.com/gsd-build/get-shit-done), a spec-driven development framework for [Claude Code](https://docs.anthropic.com/en/docs/claude-code). GSD handles the full lifecycle — requirements gathering, phased roadmaps, atomic execution with per-task commits, and verification — so you can ship complex projects without losing the thread.

## License

MIT
