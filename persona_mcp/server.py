"""FastMCP server exposing a single persona's tools over stdio.

No LLM API key needed anywhere - ask_persona returns context (style, bio,
memories) as data; the host model (Claude Desktop, etc.) writes the actual
in-character reply itself using its own model access. Vector memory lives in
Qdrant (see persona_mcp/storage.py), so QDRANT_URL/QDRANT_API_KEY must be set.

Run (directly, for local testing):
    uv run fastmcp dev persona_mcp/server.py

Run (as Claude Desktop will launch it):
    PERSONA_NAME=<name> uv run python -m persona_mcp.server

Multi-tenant note: this currently loads ONE persona from module-level
singletons via the PERSONA_NAME env var. The hosted multi-tenant version
(webapp/mcp_multi.py) instead resolves the persona per-request from a URL
token, backed by Supabase instead of local bio/style-profile files.
"""

import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastmcp import FastMCP

from persona_mcp import config, storage
from persona_mcp.prompts import build_persona_context
from persona_mcp.style_profile import load_style_profile

load_dotenv()

PERSONA_NAME = os.environ.get("PERSONA_NAME")
if not PERSONA_NAME:
    raise RuntimeError(
        "PERSONA_NAME environment variable is required. "
        "Set it in your Claude Desktop MCP server config, or export it before running."
    )

_style_profile_path = config.get_style_profile_path(PERSONA_NAME)
if not _style_profile_path.exists():
    raise RuntimeError(
        f"No style profile found at {_style_profile_path}. "
        f"Run ingestion first: uv run python -m persona_mcp.ingest --persona \"{PERSONA_NAME}\""
    )
style_profile = load_style_profile(_style_profile_path)

_bio_path = config.get_bio_path(PERSONA_NAME)
bio_text = _bio_path.read_text(encoding="utf-8") if _bio_path.exists() else ""

collection = storage.get_collection(PERSONA_NAME)

SERVER_INSTRUCTIONS = f"""This server gives you access to {PERSONA_NAME}, a real person, via
their saved memories and speaking style. Call `ask_persona` to get everything
you need to respond as them - it returns their style, bio, and relevant
memories as DATA, not a finished reply. You (the host model) write the actual
in-character response yourself using that context. Use `recall_memory` when
you just want raw facts, and `get_biography` for background.

This server also lets you WRITE new memories back, so {PERSONA_NAME} is not
stuck with amnesia between sessions - even a session on a different account:

- Call `remember` whenever the user shares something new and worth keeping
  (a life event, a preference, news, something about their relationship with
  {PERSONA_NAME}). Do this proactively during the conversation, not just when
  asked to.
- You MUST call `save_session_summary` at the end of this conversation, or as
  soon as the user indicates they are wrapping up / saying goodbye, with a
  short summary of what was discussed and anything {PERSONA_NAME} should
  remember next time. Do this even if you already called `remember` during
  the conversation - the summary captures the overall arc, not just individual
  facts.
"""

mcp = FastMCP(f"persona-{PERSONA_NAME}", instructions=SERVER_INSTRUCTIONS)


@mcp.tool
def ask_persona(message: str) -> dict:
    """Get everything needed to respond to a message AS the persona: their
    speaking style, bio, and memories relevant to this specific message -
    returned as DATA, not a finished reply. YOU must write the actual
    in-character response yourself using this context, in first person, as
    if you ARE this person - do not just relay the returned fields as-is.
    """
    memory_chunks = storage.query_memories(collection, message, k=5)
    context = build_persona_context(PERSONA_NAME, style_profile, bio_text, memory_chunks)
    return {"persona_name": PERSONA_NAME, "context_for_reply": context}


@mcp.tool
def recall_memory(query: str, k: int = 5) -> list[str]:
    """Retrieve raw memory snippets relevant to a query, with no persona
    framing. Useful when you want the underlying facts themselves rather
    than material to write an in-character reply.
    """
    results = storage.query_memories(collection, query, k=k)
    return [r["text"] for r in results]


@mcp.tool
def get_biography() -> str:
    """Return the persona's static biography / relationship context."""
    return bio_text


@mcp.tool
def remember(fact: str) -> str:
    """Save a new fact, event, or detail from THIS conversation into the
    persona's long-term memory, so it can be recalled in future sessions -
    even from a different Claude/ChatGPT account. Call this proactively
    whenever the user shares something new and worth keeping (a life event,
    a preference, a piece of news, something about their relationship with
    the persona). Do not call this for trivial small talk.
    """
    if storage.is_near_duplicate(collection, fact):
        return "Already remembered something very similar, skipped."
    storage.add_memory(
        collection,
        fact,
        metadata={
            "source": "live_session",
            "type": "fact",
            "saved_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return "Saved to long-term memory."


@mcp.tool
def save_session_summary(summary: str) -> str:
    """Call this once, at the end of the conversation (or as soon as the user
    indicates they're finishing up / saying goodbye), to save a short
    breakdown of what was discussed this session and anything worth
    remembering or following up on next time. Use this in addition to, not
    instead of, calling `remember` for individual facts during the
    conversation.
    """
    storage.add_memory(
        collection,
        summary,
        metadata={
            "source": "live_session",
            "type": "session_summary",
            "saved_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return "Session summary saved."


if __name__ == "__main__":
    mcp.run()
