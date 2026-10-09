"""Filesystem layout for persona data: personas/<name>/{raw,chroma,style_profile.json,bio.txt}."""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

BIO_PLACEHOLDER = """\
Write a short biography / relationship context for this persona here.

Who are they? How do you know them? What should the assistant know about
them that might not show up in the chat history itself (relationships,
background, important life events, inside jokes)?
"""


def get_personas_root() -> Path:
    root = os.environ.get("PERSONA_MCP_HOME")
    return Path(root) if root else REPO_ROOT / "personas"


def get_persona_dir(persona_name: str) -> Path:
    return get_personas_root() / persona_name


def get_raw_dir(persona_name: str) -> Path:
    return get_persona_dir(persona_name) / "raw"


def get_chroma_dir(persona_name: str) -> Path:
    return get_persona_dir(persona_name) / "chroma"


def get_style_profile_path(persona_name: str) -> Path:
    return get_persona_dir(persona_name) / "style_profile.json"


def get_bio_path(persona_name: str) -> Path:
    return get_persona_dir(persona_name) / "bio.txt"


def ensure_persona_scaffold(persona_name: str) -> None:
    get_raw_dir(persona_name).mkdir(parents=True, exist_ok=True)
    get_chroma_dir(persona_name).mkdir(parents=True, exist_ok=True)
    bio_path = get_bio_path(persona_name)
    if not bio_path.exists():
        bio_path.write_text(BIO_PLACEHOLDER, encoding="utf-8")
