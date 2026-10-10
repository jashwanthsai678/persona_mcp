# JAHA

**Personal AI Memory Connector** — Journey &middot; Autobiographical &middot; Human &middot; AI
*"Keep their memories connected."*

**Live:** https://persona-mcp.onrender.com (try the [demo](https://persona-mcp.onrender.com/demo) first)

## What this actually is

You upload chat history (a WhatsApp export, notes, whatever) for someone who
matters to you. The platform learns how they write and what they talk about,
then hands you a private link. Paste that link into Claude, ChatGPT, or any
MCP-compatible client as a remote connector, and that person becomes part of
your conversation — not a chatbot you query *about* them, but something that
responds *as* them, in their own voice, drawing on real things they said.

It keeps growing: things that come up in a conversation can be saved back
into the same memory the original upload built, so a session next week (even
from a different device or account) can still "know" what happened — as
long as whoever's chatting reconnects with the same link.

## How it's built, at a high level

The core trick is that this backend **never generates text itself.** When
Claude calls this server's `ask_persona` tool, the server doesn't call an
LLM and hand back a finished reply — it retrieves the relevant memories,
the person's style profile, and their bio, and returns all of that as
**data**. Claude (the host model, using the end user's own account) reads
that data and writes the actual in-character reply itself. That one design
choice is why this backend needs **zero LLM API key anywhere** — ingestion
and style-profile extraction are both heuristic/local too, not LLM calls.

Everything durable lives in two external services, not on disk, so the app
survives a redeploy or a free-tier spin-down with nothing lost:

- **Supabase** (Postgres) — accounts, and per-persona metadata: name, status,
  bio text, the style profile (tone/catchphrases/etc., as JSON), and the
  secret token that *is* that persona's MCP URL.
- **Qdrant** — one vector collection per persona. Every memory (from the
  original upload, or saved later from a live conversation) is a point in
  that collection, embedded locally via `fastembed` with no API cost.

A single FastMCP server, mounted at `/mcp/{token}`, is shared by every
persona on the platform. Every tool call reads the token out of the live
request's URL, looks up which persona it belongs to in Supabase, and
operates only on that persona's Qdrant collection — there's no per-tenant
process, just per-request resolution.

## The tools a connected persona exposes

- `ask_persona(message)` — the main one. Returns style + bio + relevant
  memories as data; Claude writes the reply.
- `recall_memory(query)` — raw memory snippets, no persona framing.
- `get_biography()` — the static bio.
- `remember(fact)` — called mid-conversation to save something new and worth
  keeping (near-duplicates are skipped automatically).
- `save_session_summary(summary)` — saves a short recap of the conversation
  so far, so the next session isn't starting from zero.

These tool descriptions are written to be followed *aggressively* — call
`ask_persona` for literally every message once connected (including a bare
"hey"), stay in character even for off-topic questions, call `remember`
throughout the conversation rather than saving it all for the end. That
phrasing exists because weaker versions of these instructions were tested
against real Claude conversations and didn't reliably trigger — see
`webapp/mcp_multi.py` and `persona_mcp/prompts.py` for the current wording.

**Honest limitations, found through actually testing this against real
Claude conversations, not just assumed:**
- MCP has no "session ended" event — whether `remember`/`save_session_summary`
  actually fire depends on the host model choosing to follow instructions,
  not a guaranteed hook. This has been observed to fail in real use even
  with strongly-worded tool descriptions.
- Voice quality depends entirely on the host model using the returned
  context well — this backend has no control over the final wording.
- The heuristic style profile (word frequency, punctuation patterns) is
  lower-fidelity than an LLM-based read of someone's writing would be — a
  deliberate tradeoff for zero API cost. It can't extract semantic
  "recurring facts" the way an LLM pass could.

## Running it locally

```powershell
uv sync
copy .env.example .env   # then fill in the values below
```

Required in `.env`:
- `WEBAPP_SECRET_KEY` — signs session cookies. Generate with
  `python -c "import secrets; print(secrets.token_hex(32))"`
- `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` — from your Supabase project's
  Settings > API. Run `webapp/supabase_schema.sql` once in the Supabase SQL
  Editor first — this backend can't create tables via the data API.
- `QDRANT_URL` / `QDRANT_API_KEY` — from your Qdrant Cloud cluster.

```powershell
uv run uvicorn webapp.main:app --reload
```

Open `http://127.0.0.1:8000`, sign up, create a persona, write a bio, upload
a chat export, wait for status to reach `ready`, then copy the MCP URL from
the persona's page into Claude's connector settings.

## Project layout

```
webapp/
  main.py              routes + mounts the MCP app at /mcp/{token}
  db.py                Supabase-backed User/Persona (accounts + metadata)
  auth.py              password hashing, session cookies
  processing.py        background ingestion job
  mcp_multi.py         the multi-tenant FastMCP app (the tools above)
  supabase_schema.sql  run once in the Supabase SQL Editor
  templates/           landing page, demo page, dashboard (Tailwind, dark)

persona_mcp/           Core library, tenant-agnostic
  whatsapp_parser.py   WhatsApp export parsing
  chunking.py          context-preserving message chunking
  storage.py           Qdrant vector store, fastembed local embeddings
  style_profile.py     heuristic (no LLM) style extraction
  prompts.py           builds the context block ask_persona returns
  ingest.py            CLI + the function the webapp calls in the background
  server.py            single-tenant stdio MCP server, for local testing
```

For quick local experiments without the web app, `persona_mcp/ingest.py` +
`persona_mcp/server.py` work standalone (same Qdrant backend, but bio/style
profile stay as local files instead of Supabase rows):

```powershell
uv run python -m persona_mcp.ingest --persona "Grandma"
uv run fastmcp dev persona_mcp/server.py
```

## Deploying (Render, or similar)

- Build: `pip install uv && uv sync --frozen`
- Start: `uv run uvicorn webapp.main:app --host 0.0.0.0 --port $PORT`
- Env vars: `WEBAPP_SECRET_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`,
  `QDRANT_URL`, `QDRANT_API_KEY`
- No persistent disk needed — Supabase and Qdrant hold everything durable.
- Free tiers spin down after inactivity; the first request after that can
  take 30-60s to wake back up.

## Other known limitations

- WhatsApp export format varies by phone locale/OS/app version — only the
  most common header formats are handled.
- Ingestion runs as an in-process background task — fine at this scale, but
  a real job queue would be the next step if usage grows.
- No billing/usage limits per user on Supabase/Qdrant usage.
