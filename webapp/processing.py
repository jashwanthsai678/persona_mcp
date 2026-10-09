"""Background ingestion job triggered after a persona's files are uploaded."""

from persona_mcp.ingest import run_ingestion
from persona_mcp.style_profile import style_profile_to_dict
from webapp import db


def process_persona_upload(persona_id: str, display_name: str) -> None:
    db.update_persona_status(persona_id, "processing", "Parsing and embedding your upload...")

    try:
        report = run_ingestion(persona_name=persona_id, display_name=display_name, reset=True)
    except Exception as exc:  # noqa: BLE001 - surface any failure to the dashboard
        db.update_persona_status(persona_id, "error", f"Ingestion failed: {exc}")
        return

    if report.chunks_created == 0:
        db.update_persona_status(
            persona_id, "error", "No messages found in the uploaded file(s). Check the format and try again."
        )
        return

    if not report.style_profile_built or report.style_profile is None:
        db.update_persona_status(
            persona_id,
            "error",
            "Couldn't identify this persona's own messages to build a style profile. "
            "Try re-uploading with a clearer export, or make sure the name you chose "
            "matches how they appear in the chat.",
        )
        return

    db.update_persona_style_profile(persona_id, style_profile_to_dict(report.style_profile))
    db.update_persona_status(
        persona_id, "ready", f"Processed {report.messages_parsed} messages into {report.chunks_created} memories."
    )
