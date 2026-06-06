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
    document.status = status
    db.add(document)
    db.flush()
    return document
