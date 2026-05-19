# Deploy Architecture

This is entirely separate from the learning app. It describes how code changes flow from a chat interface to production without ever touching a terminal.

---

## The Problem

Current workflow:
```
write code in terminal → deploy to server → test
```

Target workflow:
```
describe change in chat → agent edits code → auto-deploy → verify in chat
```

---

## Components

### 1. Chat Interface (Discord — dedicated dev server)
Separate Discord server from the learning bot. Channels:

- **`#features`** — describe what you want built, discuss ideas, agent creates GitHub issues from this
- **`#dev`** — code change instructions ("fix X", "change Y behavior"). Agent edits, tests, pushes.
- **`#deploy`** — trigger deploys, see status, rollback
- **`#bugs`** — report something broken. Agent triages, fixes or files a GitHub issue.
- **`#logs`** — agent posts here automatically: test results, errors, deploy confirmations. Read-only for you.

### 2. Coding Agent (runs on a server, always on)
Listens for instructions, and:
- Reads the codebase
- Makes the requested edits
- Runs tests
- Commits and pushes to GitHub
- Reports results back

**Chosen approach: Claude Code CLI on a VPS, triggered by Discord #admin channel.**
- Already using Discord for the learning bot — no new app or context switching
- Claude Code CLI has full codebase access, can run tests, understands context across files
- VPS runs always-on, Discord webhook passes #admin messages to Claude Code

### 3. GitHub (source of truth)
All code lives here. The coding agent pushes changes. Nothing deploys directly — GitHub is the gatekeeper.

### 4. Auto-deploy (Railway or Render)
Watches the GitHub repo. Any push to `main` triggers an automatic redeploy. No manual deploy step ever.

### 5. Verification
After deploy, agent reports back to Discord:
- Deploy succeeded / failed
- Test results
- What changed

---

## Full Flow

```
You (phone, Discord #admin)
    → "add X feature"
    ↓
Discord #admin → VPS
    ↓
Claude Code CLI
    → reads codebase
    → makes edits
    → runs tests
    → pushes to GitHub
    ↓
GitHub → triggers Railway/Render
    ↓
Auto-deploy → new version live
    ↓
Claude Code reports back to Discord #admin
    → "Done. Tests passed. Deployed."
```

---

## Key Decision: What Is the Coding Agent?

Three realistic options:

### Option A: Claude Code on a VPS
- Run Claude Code CLI on a remote server
- Expose via webhook, triggered by Discord messages
- Pros: uses tooling already familiar, highly capable, no new framework
- Cons: requires some setup to expose Claude Code as a webhook listener

### Option B: OpenClaw
- Purpose-built for chat-controlled development workflows
- Has Discord integration, file editing, deploy orchestration built in
- Pros: less to build, designed for this exact pattern
- Cons: new framework to learn, less control, potential framework churn

### Option C: Custom lightweight agent
- Discord bot that passes #admin messages to Claude API
- Claude API gets MCP tools: filesystem read/write, git, shell (test runner)
- Fully custom, no framework dependency
- Pros: simple, fully owned, no external framework risk
- Cons: more to build upfront

**Recommendation: Option B — Claude Code CLI on a remote server, triggered from phone.**

Frequent natural-language changes ("change this wording", "add X behavior", "create a weekly report template") require a real agent with full codebase access, not a one-off terminal session. Option A (Claude.ai + GitHub MCP) is too limited for this volume and complexity. Option C (terminal when needed) creates exactly the friction we're trying to eliminate.

The trigger interface doesn't need to be Discord — a Telegram bot is simpler to set up and works well from phone.

---

## What "No Terminal" Actually Means

The server running the coding agent needs to be set up once. That initial setup requires terminal access. After that, everything is chat-driven.

One-time terminal work:
- Provision a VPS (or use Railway/Render for the agent too)
- Clone the repo
- Install Claude Code CLI / set up the agent
- Configure Discord webhook
- Set environment variables

After that: never again.

---

## V1 Scope

Minimum to validate the loop:
1. GitHub repo with auto-deploy to Railway wired up
2. Coding agent listening on Discord #admin
3. Agent can: read files, make edits, run tests, push to GitHub
4. Agent reports back to Discord on success/failure

Everything else (smarter test reporting, rollback, staged deploys) comes later.
