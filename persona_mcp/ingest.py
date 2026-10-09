"""CLI: parse raw persona data, chunk + embed it, and build a style profile.

Usage:
    uv run python -m persona_mcp.ingest --persona "<name>" [--target-sender "<name>"]
"""

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import anthropic

from persona_mcp import config, storage
from persona_mcp.chunking import chunk_messages, chunk_notes
from persona_mcp.style_profile import build_style_profile, sample_messages_for_profile, save_style_profile
from persona_mcp.whatsapp_parser import parse_whatsapp_export

# WhatsApp headers contain a date/time + " - " separator before "Sender: text".
_WHATSAPP_HEADER_HINT_RE = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}.{0,20}[-–].{0,40}:")


@dataclass
class IngestionReport:
    messages_parsed: int = 0
    messages_skipped: int = 0
    chunks_created: int = 0
    style_profile_built: bool = False
    sample_chunks: list[str] | None = None


def _looks_like_whatsapp(file_path: Path) -> bool:
    try:
        head = file_path.read_text(encoding="utf-8-sig", errors="ignore")[:2000]
    except OSError:
        return False
    return bool(_WHATSAPP_HEADER_HINT_RE.search(head))


def discover_source_files(raw_dir: Path) -> tuple[list[Path], list[Path]]:
    whatsapp_files: list[Path] = []
    note_files: list[Path] = []
    for f in sorted(raw_dir.glob("*")):
        if not f.is_file():
            continue
        if f.suffix.lower() not in (".txt", ".md"):
            continue
        if _looks_like_whatsapp(f):
            whatsapp_files.append(f)
        else:
            note_files.append(f)
    return whatsapp_files, note_files


def _infer_target_sender(messages, display_name: str) -> str:
    """Best-effort guess at which sender in the export is the persona, by matching
    the display name against sender names (case-insensitive substring match).
    Falls back to the most frequent sender if no match is found."""
    senders = {}
    for m in messages:
        senders[m.sender] = senders.get(m.sender, 0) + 1

    name_lower = display_name.strip().lower()
    for sender in senders:
        if name_lower in sender.lower() or sender.lower() in name_lower:
            return sender

    return max(senders, key=senders.get) if senders else display_name


def run_ingestion(
    persona_name: str,
    display_name: str | None = None,
    raw_dir: Path | None = None,
    target_sender: str | None = None,
    reset: bool = False,
    skip_style_profile: bool = False,
    max_sample_messages: int = 300,
) -> IngestionReport:
    """persona_name is the storage key (directory/collection name - a UUID for
    multi-tenant use). display_name is the human-readable name shown to the LLM
    in prompts and used to auto-detect the target sender; defaults to
    persona_name for the single-tenant local CLI flow."""
    display_name = display_name or persona_name
    config.ensure_persona_scaffold(persona_name)
    raw_dir = raw_dir or config.get_raw_dir(persona_name)

    report = IngestionReport()
    collection = storage.reset_collection(persona_name) if reset else storage.get_collection(persona_name)

    whatsapp_files, note_files = discover_source_files(raw_dir)
    if not whatsapp_files and not note_files:
        print(f"No .txt/.md files found in {raw_dir}. Drop a WhatsApp export or notes there first.")
        return report

    all_target_messages = []

    for wf in whatsapp_files:
        result = parse_whatsapp_export(wf)
        report.messages_parsed += len(result.messages)
        report.messages_skipped += result.lines_skipped

        sender = target_sender or _infer_target_sender(result.messages, display_name)
        chunks = chunk_messages(result.messages, target_person=sender, source_file=wf.name)
        storage.add_chunks(collection, chunks)
        report.chunks_created += len(chunks)
        if report.sample_chunks is None and chunks:
            report.sample_chunks = [c.text for c in chunks[:2]]

        all_target_messages.extend(
            [m for m in result.messages if m.sender.strip().lower() == sender.strip().lower()]
        )

    for nf in note_files:
        text = nf.read_text(encoding="utf-8-sig", errors="ignore")
        chunks = chunk_notes(text, source_file=nf.name)
        storage.add_chunks(collection, chunks)
        report.chunks_created += len(chunks)

    if not skip_style_profile and all_target_messages:
        client = anthropic.Anthropic()
        sampled = sample_messages_for_profile(all_target_messages, max_sample_messages)
        if sampled:
            profile = build_style_profile(sampled, display_name, client)
            save_style_profile(profile, config.get_style_profile_path(persona_name))
            report.style_profile_built = True

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest raw persona data into local memory + style profile.")
    parser.add_argument("--persona", required=True, help="Persona name (used for data directory + collection)")
    parser.add_argument("--raw-dir", type=Path, default=None, help="Override raw data directory")
    parser.add_argument("--target-sender", default=None, help="Exact sender name in the export to treat as the persona (auto-detected if omitted)")
    parser.add_argument("--reset", action="store_true", help="Wipe existing vector store before ingesting")
    parser.add_argument("--skip-style-profile", action="store_true", help="Skip the Anthropic API call for style extraction")
    parser.add_argument("--max-sample-messages", type=int, default=300)
    args = parser.parse_args(argv)

    report = run_ingestion(
        persona_name=args.persona,
        raw_dir=args.raw_dir,
        target_sender=args.target_sender,
        reset=args.reset,
        skip_style_profile=args.skip_style_profile,
        max_sample_messages=args.max_sample_messages,
    )

    print(f"Persona: {args.persona}")
    print(f"Messages parsed: {report.messages_parsed}  (skipped: {report.messages_skipped})")
    print(f"Chunks created: {report.chunks_created}")
    print(f"Style profile built: {report.style_profile_built}")
    if report.sample_chunks:
        print("\nSample chunk preview:")
        for i, text in enumerate(report.sample_chunks, 1):
            preview = text if len(text) <= 300 else text[:300] + "..."
            print(f"  [{i}] {preview}")

    bio_path = config.get_bio_path(args.persona)
    print(f"\nBio file: {bio_path} (edit this by hand if you haven't already)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
