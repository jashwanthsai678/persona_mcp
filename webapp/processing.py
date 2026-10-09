"""Background ingestion job triggered after a persona's files are uploaded."""

from persona_mcp.ingest import run_ingestion
from webapp.db import Persona, get_session


def process_persona_upload(persona_id: str, display_name: str) -> None:
    db = get_session()
    try:
        persona = db.get(Persona, persona_id)
        if persona is None:
            return
        persona.status = "processing"
        persona.status_message = "Parsing and embedding your upload..."
        db.commit()

        try:
            report = run_ingestion(persona_name=persona_id, display_name=display_name, reset=True)
        except Exception as exc:  # noqa: BLE001 - surface any failure to the dashboard
            persona.status = "error"
            persona.status_message = f"Ingestion failed: {exc}"
            db.commit()
            return

        if report.chunks_created == 0:
            persona.status = "error"
            persona.status_message = "No messages found in the uploaded file(s). Check the format and try again."
        elif not report.style_profile_built:
            persona.status = "error"
            persona.status_message = (
                "Couldn't identify this persona's own messages to build a style profile. "
                "Try re-uploading with a clearer export, or make sure the name you chose "
                "matches how they appear in the chat."
            )
        else:
            persona.status = "ready"
            persona.status_message = (
                f"Processed {report.messages_parsed} messages into {report.chunks_created} memories."
            )
        db.commit()
    finally:
        db.close()
