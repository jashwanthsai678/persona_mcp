"""One-time LLM extraction of a persona's tone/vocabulary/style from sample messages."""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import anthropic

from persona_mcp.prompts import build_style_profile_prompt
from persona_mcp.whatsapp_parser import Message

MODEL = "claude-sonnet-5-5"


@dataclass
class StyleProfile:
    tone: str
    vocabulary_notes: str
    catchphrases: list[str] = field(default_factory=list)
    message_length_habits: str = ""
    punctuation_habits: str = ""
    recurring_facts: list[str] = field(default_factory=list)


def sample_messages_for_profile(messages: list[Message], max_messages: int = 300) -> list[Message]:
    """Stratified sample of the target person's messages, spread across the
    whole timeline rather than just the first N, so the profile captures
    variety rather than one period's mood. Caller is expected to have already
    filtered `messages` down to the target person."""
    if len(messages) <= max_messages:
        return messages

    step = len(messages) / max_messages
    indices = [int(i * step) for i in range(max_messages)]
    return [messages[i] for i in indices]


def build_style_profile(
    sampled_messages: list[Message], display_name: str, client: anthropic.Anthropic
) -> StyleProfile:
    sample_text = "\n".join(f"{m.sender}: {m.text}" for m in sampled_messages)
    prompt = build_style_profile_prompt(display_name, sample_text)

    response = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )

    raw_text = response.content[0].text.strip()
    # Model may wrap the JSON in a code fence despite instructions; strip it defensively.
    if raw_text.startswith("```"):
        raw_text = raw_text.strip("`")
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
        raw_text = raw_text.strip()

    data = json.loads(raw_text)
    return StyleProfile(
        tone=data.get("tone", ""),
        vocabulary_notes=data.get("vocabulary_notes", ""),
        catchphrases=data.get("catchphrases", []),
        message_length_habits=data.get("message_length_habits", ""),
        punctuation_habits=data.get("punctuation_habits", ""),
        recurring_facts=data.get("recurring_facts", []),
    )


def save_style_profile(profile: StyleProfile, path: Path) -> None:
    path.write_text(json.dumps(asdict(profile), indent=2), encoding="utf-8")


def load_style_profile(path: Path) -> StyleProfile:
    data = json.loads(path.read_text(encoding="utf-8"))
    return StyleProfile(**data)
