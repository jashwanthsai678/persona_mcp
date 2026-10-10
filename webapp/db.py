"""Supabase-backed storage for user accounts and persona metadata.

Persona memory itself lives in Qdrant (see persona_mcp.storage) - this module
only holds accounts, persona identity/status, bio text, and the style
profile JSON. None of it lives in local files or a local DB anymore, which is
what lets this survive a Render redeploy or free-tier spin-down.
"""

import os
import secrets
from dataclasses import dataclass

from supabase import Client, create_client

_client: Client | None = None


def _get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    return _client


@dataclass
class User:
    id: str
    email: str
    password_hash: str
    created_at: str | None = None


@dataclass
class Persona:
    id: str
    owner_id: str
    name: str
    mcp_token: str
    status: str = "created"
    status_message: str = ""
    bio_text: str = ""
    style_profile: dict | None = None
    created_at: str | None = None
    updated_at: str | None = None


def _user_from_row(row: dict) -> User:
    return User(id=row["id"], email=row["email"], password_hash=row["password_hash"], created_at=row.get("created_at"))


def _persona_from_row(row: dict) -> Persona:
    return Persona(
        id=row["id"],
        owner_id=row["owner_id"],
        name=row["name"],
        mcp_token=row["mcp_token"],
        status=row.get("status") or "created",
        status_message=row.get("status_message") or "",
        bio_text=row.get("bio_text") or "",
        style_profile=row.get("style_profile"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


# --- Users -------------------------------------------------------------


def create_user(email: str, password_hash: str) -> User:
    result = _get_client().table("users").insert({"email": email, "password_hash": password_hash}).execute()
    return _user_from_row(result.data[0])


def get_user_by_email(email: str) -> User | None:
    result = _get_client().table("users").select("*").eq("email", email).limit(1).execute()
    return _user_from_row(result.data[0]) if result.data else None


def get_user_by_id(user_id: str) -> User | None:
    result = _get_client().table("users").select("*").eq("id", user_id).limit(1).execute()
    return _user_from_row(result.data[0]) if result.data else None


# --- Personas ------------------------------------------------------------


def create_persona(owner_id: str, name: str) -> Persona:
    token = secrets.token_hex(32)
    result = (
        _get_client()
        .table("personas")
        .insert({"owner_id": owner_id, "name": name, "mcp_token": token, "status": "created"})
        .execute()
    )
    return _persona_from_row(result.data[0])


def get_persona(persona_id: str) -> Persona | None:
    result = _get_client().table("personas").select("*").eq("id", persona_id).limit(1).execute()
    return _persona_from_row(result.data[0]) if result.data else None


def get_persona_by_token(token: str) -> Persona | None:
    result = _get_client().table("personas").select("*").eq("mcp_token", token).limit(1).execute()
    return _persona_from_row(result.data[0]) if result.data else None


def get_personas_for_owner(owner_id: str) -> list[Persona]:
    result = (
        _get_client()
        .table("personas")
        .select("*")
        .eq("owner_id", owner_id)
        .order("created_at", desc=True)
        .execute()
    )
    return [_persona_from_row(row) for row in result.data]


def update_persona_status(persona_id: str, status: str, status_message: str = "") -> None:
    _get_client().table("personas").update({"status": status, "status_message": status_message}).eq(
        "id", persona_id
    ).execute()


def update_persona_bio(persona_id: str, bio_text: str) -> None:
    _get_client().table("personas").update({"bio_text": bio_text}).eq("id", persona_id).execute()


def update_persona_style_profile(persona_id: str, style_profile: dict) -> None:
    _get_client().table("personas").update({"style_profile": style_profile}).eq("id", persona_id).execute()


def delete_persona(persona_id: str) -> None:
    _get_client().table("personas").delete().eq("id", persona_id).execute()


# --- Conversation logs (for future fine-tuning data, not retrieval) -------


def log_exchange(persona_id: str, user_message: str, persona_reply: str) -> None:
    _get_client().table("conversation_logs").insert(
        {"persona_id": persona_id, "user_message": user_message, "persona_reply": persona_reply}
    ).execute()


def get_conversation_logs(persona_id: str, limit: int = 1000) -> list[dict]:
    result = (
        _get_client()
        .table("conversation_logs")
        .select("*")
        .eq("persona_id", persona_id)
        .order("created_at")
        .limit(limit)
        .execute()
    )
    return result.data
