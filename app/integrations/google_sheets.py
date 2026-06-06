from googleapiclient.discovery import build

from app.applications.models import Application
from app.config import get_settings
from app.drivers.models import Driver
from app.integrations.google_common import get_google_credentials


APPLICATION_HEADERS = [
    "Дата заявки",
    "WhatsApp номер",
    "ФИО",
    "Телефон",
    "Город",
    "ИИН",
    "Марка авто",
    "Модель авто",
    "Год авто",
    "Госномер",
    "Цвет",
    "Права лицевая сторона",
    "Права обратная сторона",
    "Удостоверение личности",
    "Техпаспорт / СТС",
    "Селфи с правами",
    "Доверенность / аренда",
    "Статус заявки",
    "Статус Yandex Fleet",
    "Yandex Driver ID",
    "Yandex Vehicle ID",
    "Ошибка Yandex",
    "Дата последнего сообщения",
    "Дата отправки в Yandex",
]

DELETION_HEADERS = [
    "Дата запроса",
    "WhatsApp номер",
    "ФИО",
    "Телефон",
    "ИИН",
    "Статус заявки",
    "Статус Yandex Fleet",
    "Причина / комментарий",
]


class GoogleSheetsClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    def sync_application(self, driver: Driver, application: Application) -> None:
        spreadsheet_id = self.settings.google_sheets_id
        if not spreadsheet_id:
            raise RuntimeError("GOOGLE_SHEETS_ID is not configured")

        service = build("sheets", "v4", credentials=get_google_credentials(), cache_discovery=False)
        sheet_name = self.settings.google_sheets_worksheet_name
        self._ensure_headers(service, spreadsheet_id, sheet_name, APPLICATION_HEADERS, "A1:X1")

        doc_map = {document.document_type: document.file_url or "" for document in driver.documents}
        vehicle = driver.vehicle
        row = [
            application.created_at.isoformat() if application.created_at else "",
            driver.whatsapp_phone or "",
            driver.full_name or "",
            driver.phone or "",
            driver.city or "",
            driver.iin or "",
            vehicle.brand if vehicle and vehicle.brand else "",
            vehicle.model if vehicle and vehicle.model else "",
            vehicle.year if vehicle and vehicle.year else "",
            vehicle.plate_number if vehicle and vehicle.plate_number else "",
            vehicle.color if vehicle and vehicle.color else "",
            doc_map.get("driver_license_front", ""),
            doc_map.get("driver_license_back", ""),
            doc_map.get("id_card", ""),
            doc_map.get("vehicle_registration_doc", ""),
            doc_map.get("selfie_with_license", ""),
            doc_map.get("rent_or_power_of_attorney", ""),
            application.status or "",
            application.yandex_status or "",
            application.yandex_driver_id or "",
            application.yandex_vehicle_id or "",
            application.yandex_error or "",
            driver.last_message_at.isoformat() if driver.last_message_at else "",
            application.sent_to_yandex_at.isoformat() if application.sent_to_yandex_at else "",
        ]
        self._upsert_row(service, spreadsheet_id, sheet_name, driver.whatsapp_phone or "", 1, row, "A:X")

    def sync_deletion_request(self, driver: Driver, application: Application) -> None:
        spreadsheet_id = self.settings.google_sheets_id
        if not spreadsheet_id:
            raise RuntimeError("GOOGLE_SHEETS_ID is not configured")

        service = build("sheets", "v4", credentials=get_google_credentials(), cache_discovery=False)
        sheet_name = self.settings.google_sheets_deletion_worksheet_name
        self._ensure_headers(service, spreadsheet_id, sheet_name, DELETION_HEADERS, "A1:H1")

        row = [
            application.updated_at.isoformat() if application.updated_at else "",
            driver.whatsapp_phone or "",
            driver.full_name or "",
            driver.phone or "",
            driver.iin or "",
            application.status or "",
            application.yandex_status or "",
            application.yandex_error or "",
        ]
        self._upsert_row(service, spreadsheet_id, sheet_name, driver.whatsapp_phone or "", 1, row, "A:H")

    def _upsert_row(
        self,
        service,
        spreadsheet_id: str,
        sheet_name: str,
        lookup_value: str,
        lookup_column_index: int,
        row: list[str],
        full_range: str,
    ) -> None:
        range_name = f"{sheet_name}!{full_range}"
        values = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=range_name)
            .execute()
            .get("values", [])
        )
        existing_row = None
        for index, existing in enumerate(values[1:], start=2):
            if len(existing) > lookup_column_index and existing[lookup_column_index] == lookup_value:
                existing_row = index
                break

        last_column = chr(ord("A") + len(row) - 1)
        body = {"values": [row]}
        if existing_row:
            service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=f"{sheet_name}!A{existing_row}:{last_column}{existing_row}",
                valueInputOption="RAW",
                body=body,
            ).execute()
            return

        service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_name}!A:{last_column}",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body=body,
        ).execute()

    def _ensure_headers(self, service, spreadsheet_id: str, sheet_name: str, headers: list[str], header_range: str) -> None:
        meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        sheet_exists = any(sheet["properties"]["title"] == sheet_name for sheet in meta.get("sheets", []))
        if not sheet_exists:
            service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": [{"addSheet": {"properties": {"title": sheet_name}}}]},
            ).execute()

        current = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=f"{sheet_name}!{header_range}")
            .execute()
            .get("values", [])
        )
        if current and current[0] == headers:
            return

        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_name}!{header_range}",
            valueInputOption="RAW",
            body={"values": [headers]},
        ).execute()
