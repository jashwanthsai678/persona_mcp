"""Parses WhatsApp chat export .txt files into Message objects.

Known limitation: WhatsApp's export format varies by phone locale, OS, and
app version (date order, AM/PM vs 24h, hyphen vs en-dash separator). This
handles the common variants; lines that don't match any known pattern are
skipped rather than crashing the whole ingestion.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

SYSTEM_LINE_SNIPPETS = (
    "Messages and calls are end-to-end encrypted",
    "<Media omitted>",
    "This message was deleted",
    "You deleted this message",
    "image omitted",
    "video omitted",
    "audio omitted",
    "sticker omitted",
    "document omitted",
    "Missed voice call",
    "Missed video call",
)

# Candidate header patterns, tried in order. Each must capture
# (date_str, time_str, sender, text).
_HEADER_PATTERNS = [
    # 12/31/24, 11:59 PM - Sender Name: message text
    re.compile(
        r"^(?P<date>\d{1,2}/\d{1,2}/\d{2,4}),\s*"
        r"(?P<time>\d{1,2}:\d{2}(?:\s?[APap][Mm])?)\s*[-–]\s*"
        r"(?P<sender>[^:]+):\s?(?P<text>.*)$"
    ),
    # [12/31/24, 23:59:59] Sender Name: message text  (iOS export style)
    re.compile(
        r"^\[(?P<date>\d{1,2}/\d{1,2}/\d{2,4}),\s*"
        r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?(?:\s?[APap][Mm])?)\]\s*"
        r"(?P<sender>[^:]+):\s?(?P<text>.*)$"
    ),
]

_DATE_FORMATS = ("%m/%d/%y", "%d/%m/%y", "%m/%d/%Y", "%d/%m/%Y")
_TIME_FORMATS = ("%H:%M", "%I:%M %p", "%I:%M%p", "%H:%M:%S", "%I:%M:%S %p")


@dataclass
class Message:
    timestamp: datetime | None
    sender: str
    text: str
    raw_line: str = ""


def _parse_datetime(date_str: str, time_str: str) -> datetime | None:
    for date_fmt in _DATE_FORMATS:
        for time_fmt in _TIME_FORMATS:
            try:
                return datetime.strptime(f"{date_str} {time_str}", f"{date_fmt} {time_fmt}")
            except ValueError:
                continue
    return None


def _try_parse_header(line: str) -> tuple[datetime | None, str, str] | None:
    for pattern in _HEADER_PATTERNS:
        match = pattern.match(line)
        if match:
            ts = _parse_datetime(match.group("date"), match.group("time"))
            sender = match.group("sender").strip()
            text = match.group("text").strip()
            return ts, sender, text
    return None


def _is_system_line(text: str) -> bool:
    return any(snippet in text for snippet in SYSTEM_LINE_SNIPPETS)


def _read_text(file_path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "latin-1"):
        try:
            return file_path.read_text(encoding=encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise ValueError(f"Could not decode {file_path} with any known encoding")


@dataclass
class ParseResult:
    messages: list[Message] = field(default_factory=list)
    lines_skipped: int = 0


def parse_whatsapp_export(file_path: Path) -> ParseResult:
    content = _read_text(file_path)
    result = ParseResult()
    current: Message | None = None

    for raw_line in content.splitlines():
        line = raw_line.strip("﻿").rstrip()
        if not line:
            continue

        header = _try_parse_header(line)
        if header is not None:
            ts, sender, text = header
            if _is_system_line(text):
                current = None
                result.lines_skipped += 1
                continue
            current = Message(timestamp=ts, sender=sender, text=text, raw_line=line)
            result.messages.append(current)
        elif current is not None:
            # Continuation of a multi-line message.
            current.text = f"{current.text}\n{line}"
        else:
            result.lines_skipped += 1

    return result
