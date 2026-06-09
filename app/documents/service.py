from sqlalchemy import select
from sqlalchemy.orm import Session

from app.documents.models import Document
from app.drivers.models import Driver


def upsert_document(
    db: Session,
    driver: Driver,
    document_type: str,
    file_url: str | None,
    google_drive_file_id: str | None,
    whatsapp_media_id: str | None,
    status: str = "uploaded",
    message_id: int | None = None,
    file_name: str | None = None,
    mime_type: str | None = None,
    storage_provider: str | None = None,
    storage_path: str | None = None,
) -> Document:
    document = db.scalar(
        select(Document).where(
            Document.driver_id == driver.id,
            Document.document_type == document_type,
        )
    )
    if not document:
        document = Document(driver_id=driver.id, document_type=document_type)
    document.file_url = file_url
    document.google_drive_file_id = google_drive_file_id
    document.whatsapp_media_id = whatsapp_media_id
    document.message_id = message_id
    document.file_name = file_name
    document.mime_type = mime_type
    document.storage_provider = storage_provider
    document.storage_path = storage_path
    document.status = status
    db.add(document)
    db.flush()
    return document
