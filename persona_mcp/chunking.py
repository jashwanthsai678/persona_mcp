"""Groups parsed messages into context-bearing chunks for embedding.

A chunk is a short run of consecutive messages (not a single message, which
is often too short to be meaningful alone) that is kept only if the target
person actually said something in it.
"""

from dataclasses import dataclass, field
from datetime import timedelta

from persona_mcp.whatsapp_parser import Message


@dataclass
class Chunk:
    text: str
    metadata: dict = field(default_factory=dict)


def chunk_messages(
    messages: list[Message],
    target_person: str,
    source_file: str,
    max_chars: int = 1500,
    time_gap_minutes: int = 30,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    buffer_lines: list[str] = []
    buffer_has_target = False
    buffer_start_ts = None
    buffer_len = 0
    last_ts = None
    max_gap = timedelta(minutes=time_gap_minutes)

    def flush():
        nonlocal buffer_lines, buffer_has_target, buffer_start_ts, buffer_len
        if buffer_lines and buffer_has_target:
            chunks.append(
                Chunk(
                    text="\n".join(buffer_lines),
                    metadata={
                        "source_file": source_file,
                        "chunk_index": len(chunks),
                        "start_ts": buffer_start_ts.isoformat() if buffer_start_ts else "",
                    },
                )
            )
        buffer_lines = []
        buffer_has_target = False
        buffer_start_ts = None
        buffer_len = 0

    for msg in messages:
        line = f"{msg.sender}: {msg.text}"
        gap_exceeded = (
            last_ts is not None and msg.timestamp is not None and (msg.timestamp - last_ts) > max_gap
        )
        len_exceeded = buffer_len + len(line) > max_chars

        if buffer_lines and (gap_exceeded or len_exceeded):
            flush()

        if not buffer_lines:
            buffer_start_ts = msg.timestamp

        buffer_lines.append(line)
        buffer_len += len(line) + 1
        if msg.sender.strip().lower() == target_person.strip().lower():
            buffer_has_target = True
        last_ts = msg.timestamp

    flush()
    return chunks


def chunk_notes(text: str, source_file: str, max_chars: int = 1500) -> list[Chunk]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[Chunk] = []
    buffer = ""

    def flush():
        nonlocal buffer
        if buffer:
            chunks.append(
                Chunk(
                    text=buffer,
                    metadata={"source_file": source_file, "chunk_index": len(chunks)},
                )
            )
        buffer = ""

    for para in paragraphs:
        if buffer and len(buffer) + len(para) + 2 > max_chars:
            flush()
        buffer = f"{buffer}\n\n{para}" if buffer else para

    flush()
    return chunks
