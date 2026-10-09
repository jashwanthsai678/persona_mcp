"""Multi-tenant FastMCP app, mounted at /mcp/{token} in webapp/main.py.

Unlike persona_mcp/server.py (one persona baked into module-level singletons,
run over stdio for local testing), this resolves which persona's data to use
on EVERY call by reading the `token` path segment out of the live HTTP
request via FastMCP's get_http_request(). That's what lets one running
server process many personas/users at once, each with their own unique
/mcp/<token> URL.
"""

from datetime import datetime, timezone

import anthropic
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request

from persona_mcp import config, storage
from persona_mcp.prompts import build_persona_system_prompt
from persona_mcp.style_profile import load_style_profile
from webapp.db import Persona, get_session

GENERATION_MODEL = "claude-sonnet-5-5"

_anthropic_client = anthropic.Anthropic()

SERVER_INSTRUCTIONS = """This server gives you access to a specific real person, via their
saved memories and speaking style. Call `get_biography` early in the
conversation if you don't already know who they are.

Use `ask_persona` to talk to them in character and get a fully in-character
reply. Use `recall_memory` when you want raw context instead of a styled
reply.

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

    db = get_session()
    try:
        persona = db.query(Persona).filter_by(mcp_token=token).first()
    finally:
        db.close()

    if persona is None:
        raise PersonaLookupError("This MCP link is invalid, or the persona has been deleted.")
    return persona


def _load_bio(persona_id: str) -> str:
    bio_path = config.get_bio_path(persona_id)
    return bio_path.read_text(encoding="utf-8") if bio_path.exists() else ""


@mcp.tool
def ask_persona(message: str) -> str:
    """Ask the persona a question or send them a message, and get back their
    fully in-character reply, written in their own voice and drawing on their
    real memories where relevant. Returns the final, ready-to-show reply as
    the persona - return this text to the user as-is, do not rewrite or
    re-voice it yourself.

    This tool only sees the `message` you pass, not the rest of the
    conversation. If you've picked up on how the user is doing right now -
    upset, stressed, busy/focused on something else, just chatting - include
    that briefly in `message` (e.g. "they seem really stressed about work
    right now" or "they're heads-down coding, just said hi in passing") so
    the persona can respond the way a real person who knows them would.
    """
    persona = _current_persona()
    if persona.status != "ready":
        return (
            f"'{persona.name}' isn't ready to talk yet (status: {persona.status}). "
            "Finish uploading and processing data for this persona on the platform first."
        )

    style_profile = load_style_profile(config.get_style_profile_path(persona.id))
    bio_text = _load_bio(persona.id)
    collection = storage.get_collection(persona.id)
    memory_chunks = storage.query_memories(collection, message, k=5)
    system_prompt = build_persona_system_prompt(persona.name, style_profile, bio_text, memory_chunks)

    response = _anthropic_client.messages.create(
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
    persona = _current_persona()
    collection = storage.get_collection(persona.id)
    results = storage.query_memories(collection, query, k=k)
    return [r["text"] for r in results]


@mcp.tool
def get_biography() -> str:
    """Return the persona's static biography / relationship context."""
    persona = _current_persona()
    bio_text = _load_bio(persona.id)
    return bio_text or f"No biography has been written yet for {persona.name}."


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
