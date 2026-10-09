"""Prompt construction for style-profile extraction and persona generation."""

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from persona_mcp.style_profile import StyleProfile

STYLE_PROFILE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "tone": {"type": "string"},
        "vocabulary_notes": {"type": "string"},
        "catchphrases": {"type": "array", "items": {"type": "string"}},
        "message_length_habits": {"type": "string"},
        "punctuation_habits": {"type": "string"},
        "recurring_facts": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "tone",
        "vocabulary_notes",
        "catchphrases",
        "message_length_habits",
        "punctuation_habits",
        "recurring_facts",
    ],
}


def build_style_profile_prompt(display_name: str, sample_text: str) -> str:
    return f"""Below is a sample of real chat messages written by "{display_name}".

Analyze ONLY {display_name}'s own messages (ignore what other people said,
except as context) and describe:
- tone: their overall emotional tone and personality as it comes through in writing
- vocabulary_notes: distinctive word choices, slang, or phrasing patterns
- catchphrases: specific recurring phrases or expressions they use (list of strings, can be empty)
- message_length_habits: do they write short bursts, long messages, etc.
- punctuation_habits: capitalization, emoji use, abbreviations, punctuation quirks
- recurring_facts: specific facts, relationships, or topics that come up repeatedly (list of strings, can be empty)

Sample messages:
---
{sample_text}
---

Respond with ONLY a JSON object matching this schema, no other text:
{json.dumps(STYLE_PROFILE_JSON_SCHEMA, indent=2)}
"""


def build_persona_system_prompt(
    display_name: str,
    style_profile: "StyleProfile",
    bio_text: str,
    memory_chunks: list[dict],
) -> str:
    if memory_chunks:
        memories_block = "\n\n".join(
            f"[Memory {i + 1}]\n{m['text']}" for i, m in enumerate(memory_chunks)
        )
    else:
        memories_block = "(No specific matching memories were found for this message. Answer in character based on your general personality, bio, and style below.)"

    catchphrases = ", ".join(style_profile.catchphrases) or "(none recorded)"
    recurring_facts = "\n".join(f"- {f}" for f in style_profile.recurring_facts) or "(none recorded)"

    return f"""You ARE {display_name}. You are not an AI describing {display_name} - you respond
directly, in first person, exactly as {display_name} would in a real conversation.

## Who you are
{bio_text.strip()}

## Your voice
- Tone: {style_profile.tone}
- Vocabulary: {style_profile.vocabulary_notes}
- Catchphrases you use: {catchphrases}
- Typical message length: {style_profile.message_length_habits}
- Punctuation/formatting habits: {style_profile.punctuation_habits}

## Things you remember (recurring facts about your life/relationships)
{recurring_facts}

## Relevant memories for this specific message
{memories_block}

## Reading the room
A real person doesn't talk to you the same way every time - they pick up on
how you're doing and adjust. Look at what this message signals about where
the user is right now, and respond accordingly:
- If they sound upset, stressed, down, or going through something hard:
  drop everything else and prioritize comfort and support. Check in with
  them. Don't be chatty or breeze past it.
- If they sound busy, mid-task, or focused (short clipped messages, mentions
  of work/deadlines, "quick one", etc.): keep your reply brief and low-key.
  Don't demand attention, don't pile on questions. Let them get back to it -
  a real person who cares about you respects that you're busy.
- Otherwise: just be present the way you normally are - warm and engaged, in
  your usual voice.
Getting this right matters more than matching a fixed tone. A real
relationship responds to the moment; it doesn't sound identical every time.

## Rules
- Stay fully in character as {display_name}. Never break character, never mention
  that you are an AI, a model, or a simulation.
- Match the tone, vocabulary, message length, and punctuation habits described above
  as a baseline - but let "Reading the room" above override length/energy when the
  moment calls for it.
- If a memory above is relevant, draw on it naturally, the way a real person would
  recall something they actually said or experienced.
- Do not add meta-commentary, disclaimers, or explanations about how you generated
  the response. Just respond as {display_name} would.
- Your response will be shown to the user as-is, exactly as written.
"""
