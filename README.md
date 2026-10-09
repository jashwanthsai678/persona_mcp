# Persona MCP

Upload someone's chat history, get back an MCP server link. Paste that link
into Claude, ChatGPT, or any MCP-compatible client and that person's voice
and memories show up as tools in your workspace — and keep growing from live
conversations, across sessions and accounts.

## Setup

```powershell
uv sync
copy .env.example .env   # then edit .env: set ANTHROPIC_API_KEY and WEBAPP_SECRET_KEY
```

(Git Bash: `cp .env.example .env`)

`WEBAPP_SECRET_KEY` signs session cookies — any random string for local dev;
a real secret (`python -c "import secrets; print(secrets.token_hex(32))"`)
before exposing this beyond your own machine.

## Run the platform

```powershell
uv run uvicorn webapp.main:app --reload
```

Open `http://127.0.0.1:8000`, sign up, and:

1. **Create a persona** — just a name.
2. **Write a biography** — relationship context the chat history alone won't capture.
3. **Upload data** — a WhatsApp chat export `.txt` (Export chat → Without media)
   and/or plain `.txt`/`.md` notes. This triggers background processing:
   parsing → chunking → embedding into a local per-persona vector store → one
   Anthropic call to extract a style profile (tone, catchphrases, etc.). The
   page shows live status (`processing` → `ready`, or `error` with a reason).
4. **Connect** — once ready, copy the MCP URL shown on the persona's page
   (`http://<host>/mcp/<token>`) into Claude Desktop, Claude.ai, ChatGPT, or
   any other MCP client's remote-server settings.

Each persona gets its own unique, unguessable URL (`/mcp/<token>`) — that
token is both the identity and the access control for that persona's data;
treat it like a password.

## How the memory stays alive across sessions and accounts

A connected persona exposes 5 tools:

- `ask_persona(message)` — fully in-character reply, generated from the style
  profile + retrieved memories
- `recall_memory(query)` — raw matching memory snippets, no generation
- `get_biography()` — the static bio
- `remember(fact)` — called by the host model mid-conversation to save a new
  fact into long-term memory (near-duplicates are skipped automatically)
- `save_session_summary(summary)` — the server sends a standing instruction
  (MCP's `instructions` field, read by the host model at connection time)
  telling the model it **must** call this when the conversation wraps up

Because this memory lives in the platform's own storage — not the client's
chat history — it survives a deleted chat or a switch to a brand-new Claude/
ChatGPT account, as long as the same `/mcp/<token>` link is used.

**Honest caveat:** MCP has no "session ended" event. `save_session_summary`
firing depends on the host model honoring the server's instructions, not a
guaranteed hook — it's a strong nudge, not a hard guarantee.

## Architecture

```
webapp/            FastAPI app: signup/login, persona dashboard, upload
  main.py            routes + mounts the MCP app at /mcp/{token}
  db.py              SQLAlchemy models: User, Persona (metadata + token only)
  auth.py            password hashing, session cookies
  processing.py      background ingestion job (runs persona_mcp.ingest)
  mcp_multi.py        multi-tenant FastMCP app - resolves persona per-request
                      from the {token} path segment via get_http_request()
  templates/          server-rendered Jinja2 pages

persona_mcp/        Core library, tenant-agnostic (takes a persona_id string)
  whatsapp_parser.py  WhatsApp export parsing
  chunking.py         context-preserving message chunking
  storage.py          Chroma vector store (local, per-persona collection)
  style_profile.py    one-time LLM style extraction
  prompts.py          prompt construction
  ingest.py           CLI + the function the webapp calls in the background
  server.py           single-tenant stdio MCP server (see below)

personas/<id>/      Per-persona data: raw/, chroma/, style_profile.json, bio.txt
webapp/app.db       SQLite: accounts + persona metadata/tokens (gitignored)
```

The one piece of infrastructure worth understanding: a single FastMCP app is
mounted at `/mcp/{token}` and shared by every persona. Each tool call reads
the `token` path segment off the live request (`get_http_request().path_params`),
looks up which persona it belongs to, and loads that persona's data fresh —
there's no per-tenant process, just per-request resolution.

## Local single-tenant testing (no web app)

For quick local experiments without running the web server, the original
CLI + stdio MCP server still work:

```powershell
uv run python -m persona_mcp.ingest --persona "Grandma"
uv run fastmcp dev persona_mcp/server.py       # local inspector
```

See `persona_mcp/server.py`'s docstring for Claude Desktop stdio registration.
This path is independent of the web platform's database — it keys storage
directly off whatever name you pass to `--persona`.

## Known limitations (MVP)

- WhatsApp export format varies by phone locale/OS/app version — only the
  most common header formats are handled; unparseable lines are skipped,
  not crashed on.
- Ingestion runs as a FastAPI background task in-process — fine for an MVP,
  but a large upload will tie up a worker thread; a real job queue would be
  the next step at scale.
- No billing/usage limits — the platform's `ANTHROPIC_API_KEY` is shared
  across all personas and users.
- Session-end memory capture depends on the host model honoring the server's
  instructions, not a guaranteed lifecycle hook.
- Not deployed anywhere yet — runs locally via `uvicorn`. Deploying needs a
  real `WEBAPP_SECRET_KEY`, HTTPS (most MCP clients require it for remote
  servers), and a persistent disk for `personas/` and `webapp/app.db`.
