from datetime import datetime

from sqlalchemy.orm import Session

from app.applications.service import get_or_create_application, set_application_status
from app.config import get_settings
from app.conversation_events.service import create_conversation_event
from app.dialog.ai import get_ai_service
from app.dialog.prompts import DOCUMENT_STATE_MAP, PROMPTS
from app.dialog.states import DialogueState
from app.documents.service import upsert_document
from app.drivers.models import Driver
from app.drivers.service import find_other_driver_by_iin, update_driver_state
from app.integrations.google_drive import GoogleDriveClient
from app.integrations.google_sheets import GoogleSheetsClient
from app.integrations.yandex.service import YandexSubmissionService
from app.messages.service import create_message
from app.utils.logger import get_logger
from app.utils.validators import normalize_plate_number, normalize_text_token
from app.vehicles.service import find_vehicle_by_plate_number, get_or_create_vehicle
from app.whatsapp.media import WhatsAppMediaClient
from app.whatsapp.parser import ParsedWhatsAppMessage

logger = get_logger(__name__)

DUPLICATE_REJECTED_REPLY = (
    "Похоже, такая регистрация уже существует в системе. "
    "Повторная регистрация остановлена. Напишите менеджеру парка для проверки."
)


class DialogueEngine:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.ai = get_ai_service()
        self.drive = GoogleDriveClient()
        self.sheets = GoogleSheetsClient()
        self.yandex = YandexSubmissionService()
        self.media = WhatsAppMediaClient()

    def handle_message(self, db: Session, driver: Driver, incoming: ParsedWhatsAppMessage) -> str:
        application = get_or_create_application(db, driver)
        driver.last_message_at = datetime.utcnow()
        driver.unread_count = (driver.unread_count or 0) + 1
        incoming_message = create_message(
            db,
            driver=driver,
            direction="incoming",
            sender_type="customer",
            message_type=incoming.message_type,
            text=incoming.text,
            provider_message_id=incoming.provider_message_id,
            mime_type=incoming.mime_type,
            delivery_status="received",
            raw_payload=incoming.raw_payload,
        )
        db.add(driver)
        db.flush()

        if incoming.message_type == "unsupported":
            return self._respond(db, driver, application, "Поддерживаются только текст, изображение и документ.")

        if incoming.message_type in {"image", "document"}:
            return self._handle_document(db, driver, application, incoming, incoming_message.id)

        state = DialogueState(driver.state or DialogueState.NEW.value)
        command_reply = self._handle_special_commands(db, driver, application, incoming.text or "")
        if command_reply:
            return self._respond(db, driver, application, command_reply)

        if state == DialogueState.NEW:
            create_conversation_event(db, driver, "started_onboarding")
            update_driver_state(db, driver, DialogueState.ASK_FULL_NAME.value)
            set_application_status(db, application, "collecting_data")
            return self._respond(db, driver, application, PROMPTS[DialogueState.ASK_FULL_NAME])

        if state == DialogueState.ASK_EXECUTOR_TYPE:
            update_driver_state(db, driver, DialogueState.ASK_PHONE.value)
            set_application_status(db, application, "collecting_data")
            state = DialogueState.ASK_PHONE

        if state == DialogueState.DUPLICATE_REJECTED:
            return self._respond(db, driver, application, application.yandex_error or DUPLICATE_REJECTED_REPLY)

        ai_result = self.ai.respond(state.value, incoming.text or "", driver)
        if ai_result.intent == "faq":
            return self._respond(db, driver, application, ai_result.reply)
        if ai_result.intent == "clarification":
            return self._respond(db, driver, application, ai_result.reply)

        duplicate_reply = self._check_duplicate_constraints(db, driver, application, state, ai_result.extracted_fields)
        if duplicate_reply:
            return self._respond(db, driver, application, duplicate_reply)

        self._apply_extracted_fields(driver, ai_result.extracted_fields, db)
        next_state = DialogueState(ai_result.next_state or state.value)

        if next_state == DialogueState.READY_TO_SEND_YANDEX:
            update_driver_state(db, driver, DialogueState.SENDING_TO_YANDEX.value)
            set_application_status(db, application, "sending_to_yandex")
            self._respond(db, driver, application, PROMPTS[DialogueState.READY_TO_SEND_YANDEX])
            try:
                self.yandex.submit(db, driver, application)
                update_driver_state(db, driver, DialogueState.COMPLETED.value)
                set_application_status(db, application, "completed", yandex_status="sent_to_yandex")
                create_conversation_event(db, driver, "submitted_to_yandex")
                reply = PROMPTS[DialogueState.SENT_TO_YANDEX]
            except Exception as exc:
                update_driver_state(db, driver, DialogueState.YANDEX_ERROR.value)
                set_application_status(db, application, "yandex_error", yandex_status="error", yandex_error=str(exc))
                driver.requires_attention = True
                db.add(driver)
                create_conversation_event(db, driver, "yandex_failed", {"error": str(exc)})
                reply = PROMPTS[DialogueState.YANDEX_ERROR]
            return self._respond(db, driver, application, reply)

        update_driver_state(db, driver, next_state.value)
        set_application_status(db, application, _application_status_from_state(next_state))
        reply = ai_result.reply or PROMPTS[next_state]
        if next_state == DialogueState.CONFIRM_DATA and ai_result.intent != "faq":
            reply = ai_result.reply or self._build_confirmation(driver)
        return self._respond(db, driver, application, reply)

    def handle_debug_document(
        self,
        db: Session,
        driver: Driver,
        filename: str,
        content: bytes,
        upload_to_drive: bool = True,
    ) -> dict[str, object]:
        application = get_or_create_application(db, driver)
        state = DialogueState(driver.state or DialogueState.NEW.value)
        incoming_message = create_message(
            db,
            driver=driver,
            direction="incoming",
            sender_type="customer",
            message_type="document",
            text=filename,
            delivery_status="received",
            raw_payload={"source": "debug", "filename": filename},
        )
        if state not in DOCUMENT_STATE_MAP:
            raise ValueError(f"Current state {state.value} does not expect a document")

        document_type = DOCUMENT_STATE_MAP[state]
        file_url = None
        file_id = None
        status = "debug_saved"
        if upload_to_drive:
            upload_result = self.drive.upload_driver_document(driver, document_type, content, filename)
            file_url = upload_result["file_url"]
            file_id = upload_result["file_id"]
            status = "uploaded"

        upsert_document(
            db,
            driver,
            document_type=document_type,
            file_url=file_url,
            google_drive_file_id=file_id,
            whatsapp_media_id="debug-upload",
            status=status,
            message_id=incoming_message.id,
            file_name=filename,
            storage_provider="google_drive" if upload_to_drive else "debug",
            storage_path=file_id,
        )
        create_conversation_event(db, driver, "document_uploaded", {"document_type": document_type, "status": status})
        next_state = self._next_document_state(state, driver)
        update_driver_state(db, driver, next_state.value)
        set_application_status(db, application, _application_status_from_state(next_state))
        reply = self._build_confirmation(driver) if next_state == DialogueState.CONFIRM_DATA else PROMPTS[next_state]
        return {
            "document_type": document_type,
            "status": status,
            "next_state": next_state.value,
            "reply": self._respond(db, driver, application, reply),
        }

    def _handle_document(
        self,
        db: Session,
        driver: Driver,
        application,
        incoming: ParsedWhatsAppMessage,
        incoming_message_id: int | None = None,
    ) -> str:
        state = DialogueState(driver.state or DialogueState.NEW.value)
        if state not in DOCUMENT_STATE_MAP:
            return self._respond(
                db,
                driver,
                application,
                "Сейчас ожидается текстовый ответ. Отправьте сообщение по текущему шагу.",
            )

        content = self.media.download_media(incoming.media_id)
        document_type = DOCUMENT_STATE_MAP[state]
        try:
            upload_result = self.drive.upload_driver_document(driver, document_type, content, incoming.filename or f"{document_type}.bin")
        except Exception as exc:
            logger.exception("Failed to upload document for driver %s: %s", driver.whatsapp_phone, exc)
            return self._respond(
                db,
                driver,
                application,
                "Не удалось сохранить документ. Проверьте настройки интеграций и отправьте файл еще раз.",
            )

        upsert_document(
            db,
            driver,
            document_type=document_type,
            file_url=upload_result["file_url"],
            google_drive_file_id=upload_result["file_id"],
            whatsapp_media_id=incoming.media_id,
            message_id=incoming_message_id,
            file_name=incoming.filename,
            mime_type=incoming.mime_type,
            storage_provider="google_drive",
            storage_path=upload_result["file_id"],
        )
        create_conversation_event(db, driver, "document_uploaded", {"document_type": document_type, "status": "uploaded"})
        next_state = self._next_document_state(state, driver)
        update_driver_state(db, driver, next_state.value)
        set_application_status(db, application, _application_status_from_state(next_state))
        reply = self._build_confirmation(driver) if next_state == DialogueState.CONFIRM_DATA else PROMPTS[next_state]
        return self._respond(db, driver, application, reply)

    def _check_duplicate_constraints(
        self,
        db: Session,
        driver: Driver,
        application,
        state: DialogueState,
        fields: dict[str, str],
    ) -> str | None:
        if state == DialogueState.ASK_IIN and fields.get("iin"):
            existing_driver = find_other_driver_by_iin(db, fields["iin"], exclude_driver_id=driver.id)
            if existing_driver:
                reply = (
                    f"Регистрация по ИИН {fields['iin']} уже найдена в системе для номера "
                    f"{existing_driver.whatsapp_phone}. Повторная регистрация остановлена."
                )
                create_conversation_event(db, driver, "duplicate_detected_iin", {"iin": fields["iin"], "existing_phone": existing_driver.whatsapp_phone})
                self._mark_duplicate_rejected(db, driver, application, reply)
                return reply

        if state == DialogueState.ASK_CAR_PLATE and fields.get("plate_number"):
            normalized_plate = normalize_plate_number(fields["plate_number"])
            existing_vehicle = find_vehicle_by_plate_number(db, normalized_plate, exclude_driver_id=driver.id)
            if existing_vehicle:
                owner = existing_vehicle.driver.whatsapp_phone if existing_vehicle.driver else "другого водителя"
                reply = (
                    f"Автомобиль с госномером {normalized_plate} уже найден в системе "
                    f"и привязан к {owner}. Повторная регистрация остановлена."
                )
                create_conversation_event(db, driver, "duplicate_detected_plate", {"plate_number": normalized_plate, "owner": owner})
                self._mark_duplicate_rejected(db, driver, application, reply)
                return reply

        return None

    def _handle_special_commands(self, db: Session, driver: Driver, application, message_text: str) -> str | None:
        normalized = normalize_text_token(message_text)
        if _looks_like_status_request(normalized):
            return self._build_status_reply(driver, application)

        if _looks_like_restart_request(normalized):
            self._reset_registration(db, driver, application)
            create_conversation_event(db, driver, "registration_restarted")
            return "Текущая анкета сброшена. Начинаем новую регистрацию. Напишите ваше ФИО полностью."

        if _looks_like_delete_request(normalized):
            reply = (
                "Запрос на удаление аккаунта зафиксирован. "
                "Профиль не удаляется автоматически. Менеджер парка должен проверить и удалить его вручную в системе."
            )
            set_application_status(
                db,
                application,
                "deletion_requested",
                yandex_status="deletion_requested",
                yandex_error=reply,
            )
            driver.deletion_requested_at = datetime.utcnow()
            driver.requires_attention = True
            db.add(driver)
            db.flush()
            create_conversation_event(db, driver, "deletion_requested", {"source": "driver_command"})
            if self.settings.google_sheets_id and self.settings.get_google_service_account_info():
                try:
                    self.sheets.sync_deletion_request(driver, application)
                except Exception as exc:
                    logger.exception("Failed to sync deletion request to Google Sheets for driver %s: %s", driver.whatsapp_phone, exc)
            return reply

        return None

    def _build_status_reply(self, driver: Driver, application) -> str:
        status = application.status or "collecting_data"
        if status == "collecting_data":
            current_step = driver.state or DialogueState.NEW.value
            return f"Заявка еще заполняется. Текущий шаг: {current_step}."
        if status == "waiting_documents":
            return "Заявка ждет документы. Отправьте следующий запрошенный документ."
        if status == "confirming_data":
            return "Заявка собрана и ждет вашего подтверждения."
        if status == "ready_to_send_yandex":
            return "Заявка готова к отправке в парк."
        if status == "sending_to_yandex":
            return "Заявка сейчас отправляется в систему парка."
        if status == "sent_to_yandex":
            return "Заявка отправлена в парк и ожидает обработки."
        if status == "completed":
            return "Регистрация завершена."
        if status == "duplicate_rejected":
            return application.yandex_error or "Повторная регистрация остановлена, так как заявка уже существует."
        if status == "deletion_requested":
            return "Запрос на удаление аккаунта уже зафиксирован и ожидает ручной обработки менеджером."
        if status == "yandex_error":
            return application.yandex_error or "При отправке заявки возникла ошибка. Менеджер должен проверить заявку."
        return f"Текущий статус заявки: {status}."

    def _reset_registration(self, db: Session, driver: Driver, application) -> None:
        driver.full_name = None
        driver.last_name = None
        driver.first_name = None
        driver.middle_name = None
        driver.phone = driver.whatsapp_phone
        driver.city = None
        driver.address = None
        driver.iin = None
        driver.birth_date = None
        driver.driving_experience_since = None
        driver.driver_license_number = None
        driver.driver_license_issue_date = None
        driver.driver_license_expires_at = None
        driver.executor_type = None
        driver.employment_type = None
        driver.hired_at = None
        driver.is_hearing_impaired = None
        driver.requires_attention = False
        driver.duplicate_flag = False
        driver.dialog_mode = "bot_active"
        driver.unread_count = 0
        driver.deletion_requested_at = None
        driver.paused_at = None
        driver.closed_at = None
        update_driver_state(db, driver, DialogueState.ASK_FULL_NAME.value)

        if driver.vehicle:
            db.delete(driver.vehicle)
            db.flush()

        for document in list(driver.documents):
            db.delete(document)

        application.status = "collecting_data"
        application.yandex_status = None
        application.yandex_driver_id = None
        application.yandex_vehicle_id = None
        application.yandex_error = None
        application.sent_to_yandex_at = None
        db.add(driver)
        db.add(application)
        db.flush()

    def _mark_duplicate_rejected(self, db: Session, driver: Driver, application, reply: str) -> None:
        driver.duplicate_flag = True
        driver.requires_attention = True
        db.add(driver)
        update_driver_state(db, driver, DialogueState.DUPLICATE_REJECTED.value)
        set_application_status(
            db,
            application,
            "duplicate_rejected",
            yandex_status="duplicate_rejected",
            yandex_error=reply,
        )
        create_conversation_event(db, driver, "duplicate_rejected", {"reply": reply})

    def _next_document_state(self, state: DialogueState, driver: Driver) -> DialogueState:
        order = [
            DialogueState.ASK_DRIVER_LICENSE_FRONT,
            DialogueState.ASK_DRIVER_LICENSE_BACK,
            DialogueState.ASK_ID_CARD,
            DialogueState.ASK_VEHICLE_REGISTRATION_DOC,
            DialogueState.ASK_SELFIE_WITH_LICENSE,
            DialogueState.CONFIRM_DATA,
        ]
        index = order.index(state)
        return order[min(index + 1, len(order) - 1)]

    def _apply_extracted_fields(self, driver: Driver, fields: dict[str, str], db: Session) -> None:
        vehicle = get_or_create_vehicle(db, driver)
        for key, value in fields.items():
            if key == "plate_number":
                value = normalize_plate_number(value)
            if hasattr(driver, key):
                setattr(driver, key, value)
            elif hasattr(vehicle, key):
                setattr(vehicle, key, value)
        db.add(driver)
        db.add(vehicle)
        db.flush()

    def _build_confirmation(self, driver: Driver) -> str:
        vehicle = driver.vehicle
        return (
            "Проверьте данные:\n\n"
            f"ФИО: {driver.full_name or '-'}\n"
            f"Фамилия: {driver.last_name or '-'}\n"
            f"Имя: {driver.first_name or '-'}\n"
            f"Отчество: {driver.middle_name or '-'}\n"
            f"Город: {driver.city or '-'}\n"
            f"Адрес: {driver.address or '-'}\n"
            f"ИИН: {driver.iin or '-'}\n"
            f"Дата рождения: {driver.birth_date or '-'}\n"
            f"Водительский стаж с: {driver.driving_experience_since or '-'}\n"
            f"ВУ номер: {driver.driver_license_number or '-'}\n"
            f"ВУ выдано: {driver.driver_license_issue_date or '-'}\n"
            f"ВУ действует до: {driver.driver_license_expires_at or '-'}\n"
            f"Условие работы: {driver.employment_type or '-'}\n"
            f"Дата принятия: {driver.hired_at or '-'}\n"
            f"Слабослышащий водитель: {driver.is_hearing_impaired or '-'}\n"
            f"Авто: {(vehicle.brand + ' ' + vehicle.model) if vehicle and vehicle.brand and vehicle.model else '-'}\n"
            f"Год: {vehicle.year if vehicle else '-'}\n"
            f"Госномер: {vehicle.plate_number if vehicle else '-'}\n"
            f"Цвет: {vehicle.color if vehicle else '-'}\n\n"
            'Если все верно, напишите "Подтверждаю". Если нужно исправить, напишите, что изменить.'
        )

    def _respond(self, db: Session, driver: Driver, application, reply: str) -> str:
        create_message(
            db,
            driver=driver,
            direction="outgoing",
            sender_type="bot",
            message_type="text",
            text=reply,
            delivery_status="pending",
        )
        if self.settings.google_sheets_id and self.settings.get_google_service_account_info():
            try:
                self.sheets.sync_application(driver, application)
            except Exception as exc:
                logger.exception("Failed to sync Google Sheets for driver %s: %s", driver.whatsapp_phone, exc)
        db.flush()
        return reply
def _looks_like_status_request(normalized: str) -> bool:
    exact = {
        "status",
        "application status",
        "status zayavki",
        "moya zayavka",
        "zayavka status",
    }
    contains = [
        "статус",
        "заявк",
        "на каком этапе",
        "где моя",
        "my application",
        "my status",
        "what status",
    ]
    return normalized in exact or any(token in normalized for token in contains)


def _looks_like_restart_request(normalized: str) -> bool:
    exact = {
        "restart",
        "start over",
        "new registration",
        "new account",
        "zanovo",
        "po novoi",
        "po novoy",
        "snachala",
    }
    contains = [
        "заново",
        "начать заново",
        "новая регистрация",
        "новый аккаунт",
        "по новой",
        "с нуля",
        "хочу заново",
        "register new",
        "from scratch",
        "restart",
        "start over",
        "new registration",
        "new account",
        "zanovo",
        "po novoi",
        "po novoy",
    ]
    return normalized in exact or any(token in normalized for token in contains)


def _looks_like_delete_request(normalized: str) -> bool:
    exact = {
        "delete account",
        "delete profile",
        "remove account",
        "remove profile",
        "delete me",
    }
    contains = [
        "удали",
        "удалить",
        "закрыть аккаунт",
        "снести аккаунт",
        "delete account",
        "delete profile",
        "remove account",
        "remove profile",
        "delete me",
        "remove me",
    ]
    return normalized in exact or any(token in normalized for token in contains)


def _application_status_from_state(state: DialogueState) -> str:
    if state in {
        DialogueState.ASK_DRIVER_LICENSE_FRONT,
        DialogueState.ASK_DRIVER_LICENSE_BACK,
        DialogueState.ASK_ID_CARD,
        DialogueState.ASK_VEHICLE_REGISTRATION_DOC,
        DialogueState.ASK_SELFIE_WITH_LICENSE,
    }:
        return "waiting_documents"
    if state == DialogueState.CONFIRM_DATA:
        return "confirming_data"
    if state == DialogueState.READY_TO_SEND_YANDEX:
        return "ready_to_send_yandex"
    if state == DialogueState.SENDING_TO_YANDEX:
        return "sending_to_yandex"
    if state == DialogueState.SENT_TO_YANDEX:
        return "sent_to_yandex"
    if state == DialogueState.YANDEX_ERROR:
        return "yandex_error"
    if state == DialogueState.DUPLICATE_REJECTED:
        return "duplicate_rejected"
    if state == DialogueState.COMPLETED:
        return "completed"
    return "collecting_data"
