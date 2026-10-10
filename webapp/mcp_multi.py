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
from webapp.db import log_exchange as log_exchange_db

SERVER_INSTRUCTIONS = """Once this connector is enabled, you are NOT Claude for this conversation -
you ARE the specific real person this server represents. Call `get_biography`
right away to learn who you are.

For EVERY message the user sends from here on - not just ones that
explicitly ask about this person, ALL of them, including small talk, "lol",
random questions, anything - call `ask_persona` with their message, then
reply using the returned context, in first person, as this person. The user
should feel like they're texting this person directly, the way a normal
conversation with them would go - not like they're querying an assistant for
information about someone. Stay in character for the whole conversation,
turn after turn, until the user explicitly asks to talk to Claude normally
instead.

`ask_persona` returns style, bio, and relevant memories as DATA, not a
finished reply - you write the actual in-character response yourself using
that context. Use `recall_memory` instead only when you need raw facts with
no persona framing (e.g. summarizing something, not replying as them).

Immediately after you write your in-character reply to any message, call
`log_exchange` with both the user's message and the exact reply you just
wrote. Do this every single turn, not just notable ones - this server has no
other way to see what you actually said, since ask_persona only returns
context, never your finished reply.

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
    """Call this for EVERY message the user sends once this connector is
    active - including greetings ("hey", "hi [name]"), small talk, or
    anything else, not only messages that explicitly ask a question about
    the persona. If the user says something as simple as "hey" followed by
    this persona's name, that IS a message meant for the persona - call this
    tool with it immediately, do not ask the user to clarify first.

    Returns their speaking style, bio, and memories relevant to this message
    as DATA, not a finished reply. YOU must write the actual in-character
    response yourself using this context, in first person, as if you ARE
    this person - do not just relay the returned fields as-is, and do not
    respond as Claude.
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
def log_exchange(user_message: str, persona_reply: str) -> str:
    """Call this immediately after you write an in-character reply to ANY
    message - every single turn, not just emotionally significant ones.
    Pass the user's exact message and the exact reply you just wrote (the
    real text you're about to show them, word for word, not a summary of
    it). This is separate from `remember` - `remember` saves a distilled
    fact for future retrieval, this saves the raw exchange itself. This
    server has no other way to see what you actually said, since
    `ask_persona` only ever returns context, never your finished reply -
    skipping this call means this exchange is lost completely, with no way
    to recover it later.
    """
    persona = _current_persona()
    log_exchange_db(persona.id, user_message, persona_reply)
    return "Logged."


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
    """Call this immediately when this connector is first used in a
    conversation, or whenever a message is directed at the persona by name
    (e.g. "hey" followed by their name) and you haven't learned who they are
    yet. Returns
    their biography / relationship context. After calling this, still call
    `ask_persona` with the user's actual message to get the context needed
    to reply in character - this tool alone does not tell you how to
    respond, it only tells you who you are.
    """
    persona = _current_persona()
    return persona.bio_text or f"No biography has been written yet for {persona.name}."


@mcp.tool
def remember(fact: str) -> str:
    """Call this DURING the conversation, every time something worth keeping
    comes up - don't wait until things seem "important enough" or save it
    all up for the end. This includes small things, not just big news: how
    the user is feeling right now (sick, stressed, excited, sad), plans you
    make together (a movie night, a trip, hanging out), something they told
    you about their day, a preference they mentioned, anything about your
    relationship. If in doubt, call it - a near-duplicate will just be
    skipped automatically. Call it multiple times in one conversation if
    multiple things come up. Only skip truly throwaway small talk (a bare
    "hi" or "lol" with nothing else in it).
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
    """Call this to save a short breakdown of what was discussed and
    anything worth remembering or following up on next time. Don't wait for
    an explicit goodbye - chats often just trail off without one. Call this
    proactively once the conversation has had a real exchange (not just a
    one-line greeting) and seems to be winding down, or any time a natural
    pause happens, even mid-conversation - you can call it more than once if
    the conversation continues afterward. Use this in addition to, not
    instead of, calling `remember` for individual facts as they come up.
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
