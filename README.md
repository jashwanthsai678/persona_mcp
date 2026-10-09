# Persona MCP

Upload someone's chat history, get back an MCP server link. Paste that link
into Claude, ChatGPT, or any MCP-compatible client and that person's style
and memories become available as tools in your workspace — and keep growing
from live conversations, across sessions and accounts.

**No LLM API key required anywhere in this backend.** `ask_persona` returns
retrieved memories + style profile + bio as data; the *host* model (the
user's own Claude/ChatGPT) writes the actual in-character reply itself,
using its own model access. This backend only ever does retrieval, never
generation.

## Setup

```powershell
uv sync
copy .env.example .env   # then fill in the values below
```

(Git Bash: `cp .env.example .env`)

Required in `.env`:
- `WEBAPP_SECRET_KEY` — signs session cookies. Generate with
  `python -c "import secrets; print(secrets.token_hex(32))"`
- `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` — from your Supabase project's
  Settings > API. Run `webapp/supabase_schema.sql` once in the Supabase SQL
  Editor before first use — this backend can't create tables via the data API.
- `QDRANT_URL` / `QDRANT_API_KEY` — from your Qdrant Cloud cluster.

## Run the platform

```powershell
uv run uvicorn webapp.main:app --reload
```

Open `http://127.0.0.1:8000`, sign up, and:

1. **Create a persona** — just a name.
2. **Write a biography** — relationship context the chat history alone won't capture.
3. **Upload data** — a WhatsApp chat export `.txt` (Export chat → Without media)
   and/or plain `.txt`/`.md` notes. This triggers background processing:
   parsing → chunking → embedding into a Qdrant collection (local embedding
   model via `fastembed`, no API key) → a heuristic (non-LLM) style-profile
   extraction. The page shows live status (`processing` → `ready`, or `error`
   with a reason).
4. **Connect** — once ready, copy the MCP URL shown on the persona's page
   (`http://<host>/mcp/<token>`) into Claude Desktop, Claude.ai, ChatGPT, or
   any other MCP client's remote-server settings.

Each persona gets its own unique, unguessable URL (`/mcp/<token>`) — that
token is both the identity and the access control for that persona's data;
treat it like a password.

## How the memory stays alive across sessions and accounts

A connected persona exposes 5 tools:

- `ask_persona(message)` — returns style profile + bio + relevant memories as
  **data**, not a finished reply. The host model writes the actual
  in-character response itself using that context.
- `recall_memory(query)` — raw matching memory snippets, no persona framing.
- `get_biography()` — the static bio.
- `remember(fact)` — called by the host model mid-conversation to save a new
  fact into long-term memory (near-duplicates are skipped automatically).
- `save_session_summary(summary)` — the server sends a standing instruction
  (MCP's `instructions` field, read by the host model at connection time)
  telling the model it **must** call this when the conversation wraps up.

Because this memory lives in Supabase + Qdrant — not the client's chat
history — it survives a deleted chat or a switch to a brand-new Claude/
ChatGPT account, as long as the same `/mcp/<token>` link is used, and it
survives a redeploy or restart of the server itself (neither service is
backed by local disk).

**Honest caveats:**
- MCP has no "session ended" event. `save_session_summary` firing depends on
  the host model honoring the server's instructions, not a guaranteed hook.
- Voice quality now depends on the host model actually using the returned
  context well. We no longer control the final wording the way a
  server-side generation call would have.
- The heuristic style profile (word frequency, punctuation patterns) is
  lower-fidelity than an LLM-based read of someone's writing would be - a
  deliberate tradeoff for zero API cost.

## Architecture

```
webapp/            FastAPI app: signup/login, persona dashboard, upload
  main.py            routes + mounts the MCP app at /mcp/{token}
  db.py              Supabase-backed User/Persona (accounts + metadata + token)
  auth.py            password hashing, session cookies
  processing.py      background ingestion job (runs persona_mcp.ingest)
  mcp_multi.py        multi-tenant FastMCP app - resolves persona per-request
                      from the {token} path segment via get_http_request()
  supabase_schema.sql  run once in the Supabase SQL Editor
  templates/          server-rendered Jinja2 pages (Tailwind CDN, dark theme)

persona_mcp/        Core library, tenant-agnostic (takes a persona_id string)
  whatsapp_parser.py  WhatsApp export parsing
  chunking.py         context-preserving message chunking
  storage.py          Qdrant vector store, fastembed local embeddings
  style_profile.py    heuristic (no LLM) style extraction
  prompts.py          builds the context block ask_persona returns
  ingest.py           CLI + the function the webapp calls in the background
  server.py           single-tenant stdio MCP server (see below)

Supabase            users, personas (metadata, bio, style_profile, mcp_token)
Qdrant              one collection per persona_id - the actual memory
```

The one piece of infrastructure worth understanding: a single FastMCP app is
mounted at `/mcp/{token}` and shared by every persona. Each tool call reads
the `token` path segment off the live request (`get_http_request().path_params`),
looks up which persona it belongs to in Supabase, and loads that persona's
Qdrant collection fresh — there's no per-tenant process, just per-request
resolution. Nothing durable lives on local disk; uploaded raw files are
transient, used only for the one ingestion run that processes them.

## Local single-tenant testing (no web app)

For quick local experiments without running the web server, the original
CLI + stdio MCP server still work - they share the same Qdrant backend as
the web app (so still need `QDRANT_URL`/`QDRANT_API_KEY`), but keep bio and
style profile as local files instead of Supabase rows:

```powershell
uv run python -m persona_mcp.ingest --persona "Grandma"
uv run fastmcp dev persona_mcp/server.py       # local inspector
```

See `persona_mcp/server.py`'s docstring for Claude Desktop stdio registration.

## Deploying (e.g. Render)

1. Push this repo to GitHub, create a Render web service from it.
2. Build command: `pip install uv && uv sync --frozen`
3. Start command: `uv run uvicorn webapp.main:app --host 0.0.0.0 --port $PORT`
4. Environment variables: `WEBAPP_SECRET_KEY`, `SUPABASE_URL`,
   `SUPABASE_SERVICE_ROLE_KEY`, `QDRANT_URL`, `QDRANT_API_KEY`.
5. No persistent disk needed - Supabase and Qdrant hold everything durable.

## Known limitations (MVP)

- WhatsApp export format varies by phone locale/OS/app version — only the
  most common header formats are handled; unparseable lines are skipped,
  not crashed on.
- Ingestion runs as a FastAPI background task in-process — fine for an MVP,
  but a large upload will tie up a worker thread; a real job queue would be
  the next step at scale.
- No billing/usage limits on Supabase/Qdrant usage per user.
- Session-end memory capture depends on the host model honoring the server's
  instructions, not a guaranteed lifecycle hook.
- The heuristic style profile can't extract semantic "recurring facts" the
  way an LLM-based pass could (that field is always empty in this mode).
