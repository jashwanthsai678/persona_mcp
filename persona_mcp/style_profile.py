"""Heuristic (no LLM, no API key) extraction of a persona's tone/vocabulary/
style from sample messages - plain word-frequency and punctuation analysis.

Lower-fidelity than an LLM-based read of someone's writing, but costs
nothing and needs no Anthropic (or any other) API key, which is the point:
the whole point of ask_persona now returning raw context instead of a
generated reply is that NOTHING in this backend should require paying for
generation. See persona_mcp/prompts.py and webapp/mcp_multi.py.
"""

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from persona_mcp.whatsapp_parser import Message

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "so", "to", "of", "in", "on",
    "at", "for", "with", "is", "are", "was", "were", "be", "been", "it", "its",
    "this", "that", "these", "those", "i", "you", "he", "she", "we", "they",
    "my", "your", "his", "her", "our", "their", "me", "him", "us", "them",
    "do", "does", "did", "have", "has", "had", "not", "just", "can", "will",
    "would", "could", "should", "im", "about", "what", "when", "then",
    "there", "here", "up", "out", "all", "get", "got", "going", "go",
    # contractions (the regex splits on apostrophes staying attached, e.g. "i'm")
    "i'm", "it's", "that's", "you're", "we're", "they're", "don't", "can't",
    "didn't", "doesn't", "wasn't", "won't", "isn't", "aren't", "i'll",
    "you'll", "i've", "you've", "there's", "what's", "let's", "he's", "she's",
}

INTERJECTIONS = {
    "omg", "lol", "lmao", "haha", "hahaha", "ugh", "yay", "wow", "ok", "okay",
    "babe", "dear", "sweetheart", "yeah", "yep", "nope", "oh", "ah",
}

EMOJI_PATTERN = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]"
)


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


def build_style_profile_heuristic(sampled_messages: list[Message]) -> StyleProfile:
    texts = [m.text for m in sampled_messages if m.text.strip()]
    if not texts:
        return StyleProfile(tone="(not enough data)", vocabulary_notes="(not enough data)")

    lengths = [len(t) for t in texts]
    avg_len = sum(lengths) / len(lengths)
    if avg_len < 40:
        length_habit = f"short bursts, averaging about {avg_len:.0f} characters per message"
    elif avg_len < 100:
        length_habit = f"medium-length messages, averaging about {avg_len:.0f} characters"
    else:
        length_habit = f"long, detailed messages, averaging about {avg_len:.0f} characters"

    n = len(texts)
    exclaim_count = sum(t.count("!") for t in texts)
    question_count = sum(t.count("?") for t in texts)
    ellipsis_count = sum(t.count("...") for t in texts)
    caps_words = sum(1 for t in texts for w in t.split() if w.isupper() and len(w) > 1 and w.isalpha())
    emoji_count = sum(len(EMOJI_PATTERN.findall(t)) for t in texts)

    punctuation_bits = []
    if exclaim_count / n > 0.3:
        punctuation_bits.append("uses exclamation points frequently")
    if question_count / n > 0.2:
        punctuation_bits.append("asks a lot of questions")
    if caps_words / n > 0.05:
        punctuation_bits.append("occasionally WRITES IN ALL CAPS for emphasis")
    if emoji_count > 0:
        punctuation_bits.append(f"uses emoji sometimes ({emoji_count} found in sample)")
    if ellipsis_count / n > 0.03:
        punctuation_bits.append("trails off with ellipses sometimes")
    punctuation_habits = "; ".join(punctuation_bits) or "fairly plain punctuation, no strong patterns detected"

    word_counter: Counter = Counter()
    bigram_counter: Counter = Counter()
    for t in texts:
        words = re.findall(r"[a-zA-Z']+", t.lower())
        word_counter.update(w for w in words if len(w) > 2 and w not in STOPWORDS)
        for i in range(len(words) - 1):
            if words[i] in STOPWORDS and words[i + 1] in STOPWORDS:
                continue
            bigram_counter[f"{words[i]} {words[i + 1]}"] += 1

    common_words = [w for w, c in word_counter.most_common(8) if c >= 2]
    vocabulary_notes = (
        "distinctive words used often: " + ", ".join(common_words)
        if common_words
        else "no strongly distinctive vocabulary detected in this sample"
    )

    interjection_hits = [w for w in INTERJECTIONS if word_counter.get(w, 0) >= 2]
    repeated_bigrams = [p for p, c in bigram_counter.most_common(10) if c >= 3]
    catchphrases = list(dict.fromkeys(interjection_hits + repeated_bigrams))[:6]

    positive_lexicon = {"love", "happy", "great", "omg", "lol", "yay", "amazing", "good", "excited", "haha", "best", "proud"}
    negative_lexicon = {"stressed", "awful", "hate", "sad", "tired", "rough", "ugh", "bad", "worried", "sick", "overwhelmed"}
    pos_count = sum(word_counter.get(w, 0) for w in positive_lexicon)
    neg_count = sum(word_counter.get(w, 0) for w in negative_lexicon)
    if pos_count > neg_count * 2:
        tone = "warm and upbeat, leans enthusiastic and affectionate in writing"
    elif neg_count > pos_count:
        tone = "shows stress/vulnerability openly in writing, not afraid to admit when things are hard"
    else:
        tone = "balanced emotional tone, mixes casual warmth with occasional venting"

    return StyleProfile(
        tone=tone,
        vocabulary_notes=vocabulary_notes,
        catchphrases=catchphrases,
        message_length_habits=length_habit,
        punctuation_habits=punctuation_habits,
        recurring_facts=[],  # heuristic analysis can't reliably extract semantic facts without an LLM
    )


def style_profile_to_dict(profile: StyleProfile) -> dict:
    return asdict(profile)


def style_profile_from_dict(data: dict) -> StyleProfile:
    return StyleProfile(**data)


def save_style_profile(profile: StyleProfile, path: Path) -> None:
    path.write_text(json.dumps(style_profile_to_dict(profile), indent=2), encoding="utf-8")


def load_style_profile(path: Path) -> StyleProfile:
    return style_profile_from_dict(json.loads(path.read_text(encoding="utf-8")))
