import json
from io import BytesIO
from datetime import datetime
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from app.applications.models import Application
from app.config import get_settings
from app.drivers.models import Driver
from app.integrations.google_common import get_google_credentials
from app.utils.logger import get_logger


logger = get_logger(__name__)


class GoogleDriveClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    def upload_driver_document(self, driver: Driver, document_type: str, content: bytes, filename: str) -> dict[str, str]:
        now = datetime.utcnow()
        folder_path = self._build_driver_folder_path(driver, now)
        safe_name = Path(filename).name
        service = build("drive", "v3", credentials=get_google_credentials(), cache_discovery=False)
        parent_id = self._get_driver_folder_id(service, driver, now)

        media = MediaIoBaseUpload(BytesIO(content), mimetype="application/octet-stream", resumable=False)
        metadata = {"name": safe_name, "parents": [parent_id]}
        try:
            created = service.files().create(
                body=metadata,
                media_body=media,
                fields="id,webViewLink,name",
                supportsAllDrives=True,
            ).execute()
            return {
                "folder_path": folder_path,
                "file_id": created["id"],
                "file_url": created.get("webViewLink", f"https://drive.google.com/file/d/{created['id']}/view"),
                "filename": created.get("name", safe_name),
            }
        except Exception as exc:
            logger.exception("Drive upload fallback for %s (%s): %s", driver.whatsapp_phone, document_type, exc)
            return {
                "folder_path": folder_path,
                "file_id": "",
                "file_url": "",
                "filename": safe_name,
            }

    def upload_application_snapshot(self, driver: Driver, application: Application) -> dict[str, str]:
        now = datetime.utcnow()
        service = build("drive", "v3", credentials=get_google_credentials(), cache_discovery=False)
        parent_id = self._get_driver_folder_id(service, driver, now)
        filename = f"application_snapshot_{now.strftime('%Y%m%d_%H%M%S')}.json"
        payload = {
            "exported_at": now.isoformat(),
            "application": {
                "id": application.id,
                "status": application.status,
                "yandex_status": application.yandex_status,
                "yandex_driver_id": application.yandex_driver_id,
                "yandex_vehicle_id": application.yandex_vehicle_id,
                "yandex_error": application.yandex_error,
            },
            "driver": {
                "whatsapp_phone": driver.whatsapp_phone,
                "full_name": driver.full_name,
                "last_name": driver.last_name,
                "first_name": driver.first_name,
                "middle_name": driver.middle_name,
                "phone": driver.phone,
                "city": driver.city,
                "address": driver.address,
                "iin": driver.iin,
                "birth_date": driver.birth_date,
                "driving_experience_since": driver.driving_experience_since,
                "driver_license_number": driver.driver_license_number,
                "driver_license_issue_date": driver.driver_license_issue_date,
                "driver_license_expires_at": driver.driver_license_expires_at,
                "employment_type": driver.employment_type,
                "hired_at": driver.hired_at,
                "is_hearing_impaired": driver.is_hearing_impaired,
                "state": driver.state,
            },
            "vehicle": {
                "brand": driver.vehicle.brand if driver.vehicle else None,
                "model": driver.vehicle.model if driver.vehicle else None,
                "year": driver.vehicle.year if driver.vehicle else None,
                "plate_number": driver.vehicle.plate_number if driver.vehicle else None,
                "color": driver.vehicle.color if driver.vehicle else None,
                "vin": driver.vehicle.vin if driver.vehicle else None,
            },
            "documents": [
                {
                    "document_type": document.document_type,
                    "file_url": document.file_url,
                    "google_drive_file_id": document.google_drive_file_id,
                    "status": document.status,
                }
                for document in driver.documents
            ],
        }
        content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        media = MediaIoBaseUpload(BytesIO(content), mimetype="application/json", resumable=False)
        metadata = {"name": filename, "parents": [parent_id]}
        created = service.files().create(
            body=metadata,
            media_body=media,
            fields="id,webViewLink,name",
            supportsAllDrives=True,
        ).execute()
        return {
            "folder_path": self._build_driver_folder_path(driver, now),
            "file_id": created["id"],
            "file_url": created.get("webViewLink", f"https://drive.google.com/file/d/{created['id']}/view"),
            "filename": created.get("name", filename),
        }

    def _build_driver_folder_path(self, driver: Driver, now: datetime) -> str:
        return f"{now.year}/{now.month:02d}/{driver.whatsapp_phone}_{(driver.full_name or 'driver').replace(' ', '_')}"

    def _get_driver_folder_id(self, service, driver: Driver, now: datetime) -> str:
        root_folder_id = self.settings.google_drive_folder_id
        if not root_folder_id:
            raise RuntimeError("GOOGLE_DRIVE_FOLDER_ID is not configured")

        parent_id = root_folder_id
        for segment in self._build_driver_folder_path(driver, now).split("/"):
            parent_id = self._get_or_create_folder(service, segment, parent_id)
        return parent_id

    def _get_or_create_folder(self, service, folder_name: str, parent_id: str) -> str:
        escaped_name = folder_name.replace("'", "\\'")
        query = (
            "mimeType='application/vnd.google-apps.folder' "
            f"and name='{escaped_name}' "
            f"and '{parent_id}' in parents and trashed=false"
        )
        response = service.files().list(
            q=query,
            fields="files(id,name)",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            pageSize=1,
        ).execute()
        files = response.get("files", [])
        if files:
            return files[0]["id"]

        created = service.files().create(
            body={
                "name": folder_name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [parent_id],
            },
            fields="id",
            supportsAllDrives=True,
        ).execute()
        return created["id"]
