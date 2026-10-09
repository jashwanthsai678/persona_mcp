"""Multi-tenant FastMCP app, mounted at /mcp/{token} in webapp/main.py.

Tools are pure lookups - no generation happens on this server. ask_persona
returns the retrieved memories + style profile + bio as a context block; the
HOST model (the user's own Claude/ChatGPT) reads it and writes the actual
in-character reply itself, using its own model access. This backend needs no
Anthropic (or any other LLM) API key anywhere - only Supabase (accounts/
persona metadata) and Qdrant (vector memory).

Tenant resolution works the same way it always has: every call reads the
`token` path segment out of the live HTTP request via FastMCP's
get_http_request(), looks up which persona owns that token, and operates
only on that persona's data.
"""

from datetime import datetime, timezone

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request

from persona_mcp import storage
from persona_mcp.prompts import build_persona_context
from persona_mcp.style_profile import style_profile_from_dict
from webapp.db import Persona, get_persona_by_token

SERVER_INSTRUCTIONS = """This server gives you access to a specific real person, via their
saved memories and speaking style. Call `get_biography` early in the
conversation if you don't already know who they are.

Call `ask_persona` to get everything you need to respond as them - it
returns their style, bio, and relevant memories as DATA, not a finished
reply. You (the host model) write the actual in-character response yourself
using that returned context, in first person, as if you ARE that person.
Use `recall_memory` instead when you just want raw facts, no persona framing.

This server also lets you WRITE new memories back, so the persona is not
stuck with amnesia between sessions - even a session on a different account:

- Call `remember` whenever the user shares something new and worth keeping
  (a life event, a preference, news, something about their relationship with
  this person). Do this proactively during the conversation, not just when
  asked to.
- You MUST call `save_session_summary` at the end of this conversation, or as
  soon as the user indicates they are wrapping up / saying goodbye, with a
  short summary of what was discussed and anything worth remembering next
  time. Do this even if you already called `remember` during the
  conversation - the summary captures the overall arc, not just individual
  facts.
"""

mcp = FastMCP("persona-platform", instructions=SERVER_INSTRUCTIONS)


class PersonaLookupError(Exception):
    """Raised when the token in the connection URL doesn't resolve to a persona.
    FastMCP turns this into a tool error result shown to the host model."""


def _current_persona() -> Persona:
    request = get_http_request()
    token = request.path_params.get("token")
    if not token:
        raise PersonaLookupError("No persona token found in the connection URL.")

    persona = get_persona_by_token(token)
    if persona is None:
        raise PersonaLookupError("This MCP link is invalid, or the persona has been deleted.")
    return persona


@mcp.tool
def ask_persona(message: str) -> dict:
    """Get everything needed to respond to a message AS the persona: their
    speaking style, bio, and memories relevant to this specific message -
    returned as DATA, not a finished reply. YOU must write the actual
    in-character response yourself using this context, in first person, as
    if you ARE this person - do not just relay the returned fields as-is to
    the user.
    """
    persona = _current_persona()
    if persona.status != "ready" or not persona.style_profile:
        return {
            "error": (
                f"'{persona.name}' isn't ready to talk yet (status: {persona.status}). "
                "Finish uploading and processing data for this persona on the platform first."
            )
        }

    style_profile = style_profile_from_dict(persona.style_profile)
    collection = storage.get_collection(persona.id)
    memory_chunks = storage.query_memories(collection, message, k=5)
    context = build_persona_context(persona.name, style_profile, persona.bio_text, memory_chunks)

    return {"persona_name": persona.name, "context_for_reply": context}


@mcp.tool
def recall_memory(query: str, k: int = 5) -> list[str]:
    """Retrieve raw memory snippets relevant to a query, with no persona
    framing. Useful when you want the underlying facts themselves rather
    than material to write an in-character reply.
    """
    persona = _current_persona()
    collection = storage.get_collection(persona.id)
    results = storage.query_memories(collection, query, k=k)
    return [r["text"] for r in results]


@mcp.tool
def get_biography() -> str:
    """Return the persona's static biography / relationship context."""
    persona = _current_persona()
    return persona.bio_text or f"No biography has been written yet for {persona.name}."


@mcp.tool
def remember(fact: str) -> str:
    """Save a new fact, event, or detail from THIS conversation into the
    persona's long-term memory, so it can be recalled in future sessions -
    even from a different Claude/ChatGPT account. Call this proactively
    whenever the user shares something new and worth keeping (a life event,
    a preference, a piece of news, something about their relationship with
    the persona). Do not call this for trivial small talk.
    """
    persona = _current_persona()
    collection = storage.get_collection(persona.id)
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
    persona = _current_persona()
    collection = storage.get_collection(persona.id)
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
