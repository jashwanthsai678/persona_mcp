"""FastMCP server exposing a single persona's tools over stdio.

Run (directly, for local testing):
    uv run fastmcp dev persona_mcp/server.py

Run (as Claude Desktop will launch it):
    PERSONA_NAME=<name> uv run python -m persona_mcp.server

Multi-tenant note: this currently loads ONE persona from module-level
singletons via the PERSONA_NAME env var. A hosted multi-tenant version would
replace these singletons with a per-request/per-connection lookup keyed by a
URL path segment or bearer token, resolving to a tenant's persona directory.
"""

import os
from datetime import datetime, timezone

import anthropic
from dotenv import load_dotenv
from fastmcp import FastMCP

from persona_mcp import config, storage
from persona_mcp.prompts import build_persona_system_prompt
from persona_mcp.style_profile import load_style_profile

load_dotenv()

PERSONA_NAME = os.environ.get("PERSONA_NAME")
if not PERSONA_NAME:
    raise RuntimeError(
        "PERSONA_NAME environment variable is required. "
        "Set it in your Claude Desktop MCP server config, or export it before running."
    )

GENERATION_MODEL = "claude-sonnet-5-5"

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
anthropic_client = anthropic.Anthropic()

SERVER_INSTRUCTIONS = f"""This server gives you access to {PERSONA_NAME}, a real person, via
their saved memories and speaking style. Use `ask_persona` to talk to them in
character, `recall_memory` for raw context, and `get_biography` for background.

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
def ask_persona(message: str) -> str:
    """Ask the persona a question or send them a message, and get back their
    fully in-character reply, written in their own voice and drawing on their
    real memories where relevant. Returns the final, ready-to-show reply as
    the persona — return this text to the user as-is, do not rewrite or
    re-voice it yourself.

    This tool only sees the `message` you pass, not the rest of the
    conversation. If you've picked up on how the user is doing right now -
    upset, stressed, busy/focused on something else, just chatting - include
    that briefly in `message` (e.g. "they seem really stressed about work
    right now" or "they're heads-down coding, just said hi in passing") so
    the persona can respond the way a real person who knows them would.
    """
    memory_chunks = storage.query_memories(collection, message, k=5)
    system_prompt = build_persona_system_prompt(PERSONA_NAME, style_profile, bio_text, memory_chunks)

    response = anthropic_client.messages.create(
        model=GENERATION_MODEL,
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": message}],
    )
    return response.content[0].text


@mcp.tool
def recall_memory(query: str, k: int = 5) -> list[str]:
    """Retrieve raw memory snippets relevant to a query, without generating an
    in-character response. Useful when you want the underlying context itself
    rather than a styled reply.
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
