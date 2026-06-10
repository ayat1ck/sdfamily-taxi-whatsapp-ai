from datetime import datetime

from sqlalchemy.orm import Session

from app.ai_traces.service import upsert_message_ai_trace
from app.applications.service import get_or_create_application, set_application_status
from app.audit.service import create_audit_log
from app.config import get_settings
from app.conversation_events.service import create_conversation_event
from app.dialog.ai import AIResult, get_ai_service
from app.dialog.prompts import (
    DOCUMENT_STATE_MAP,
    PROMPTS,
    STATUS_COLLECTING_DATA_TEMPLATE,
    STATUS_FALLBACK_TEMPLATE,
    STATUS_REPLIES,
    YANDEX_PRO_INSTALL_TEMPLATE,
    YANDEX_PRO_START_TEMPLATE,
)
from app.dialog.states import DialogueState
from app.documents.service import upsert_document
from app.drivers.models import Driver
from app.drivers.service import find_other_driver_by_iin, update_driver_state
from app.integrations.google_drive import GoogleDriveClient
from app.integrations.google_sheets import GoogleSheetsClient
from app.integrations.yandex.catalog import resolve_brand_input, resolve_model_input
from app.integrations.yandex.client import YandexPartialSubmissionError
from app.integrations.yandex.messages import (
    build_yandex_error_reply,
    format_validation_errors_for_user,
    format_yandex_error_for_user,
)
from app.integrations.yandex.service import YandexSubmissionService
from app.messages.service import create_message
from app.utils.logger import get_logger
from app.utils.validators import normalize_plate_number, normalize_registration_certificate, normalize_text_token
from app.utils.validators import normalize_car_brand, normalize_car_model
from app.vehicles.service import find_vehicle_by_plate_number, get_or_create_vehicle
from app.whatsapp.media import WhatsAppMediaClient
from app.whatsapp.parser import ParsedWhatsAppMessage

logger = get_logger(__name__)

DUPLICATE_REJECTED_REPLY = (
    "Похоже, такая регистрация уже существует в системе. "
    "Повторная регистрация остановлена. Напишите менеджеру парка для проверки."
)

YANDEX_PRO_SUCCESS_KEYWORDS = {
    "вошел",
    "вошёл",
    "voshyol",
    "voshel",
    "voshol",
    "gotovo",
    "готово",
    "получилось",
    "авторизовался",
    "зашел",
    "зашёл",
}

SUPPORT_FLOWS = {
    "yandex_login": {
        "intro": "Помогу со входом в Яндекс Про. Пройдем шаги по порядку.",
        "reply": "Сейчас разберем вход в Яндекс Про пошагово.",
        "completed": "Отлично. Если вход выполнен и приложение открылось, можете выходить на линию. Если что-то еще мешает, напишите, что именно.",
        "steps": [
            "Откройте Яндекс Про и проверьте, что входите по тому же номеру телефона, который указывали в анкете.",
            "Если приложение просит код, дождитесь SMS и введите код подтверждения без ошибок.",
            "После входа проверьте, открывается ли главный экран водителя и видны ли рабочие разделы.",
        ],
    },
    "yandex_sms": {
        "intro": "Помогу, если не приходит SMS от Яндекс Про.",
        "reply": "Проверим по шагам, почему не приходит SMS.",
        "completed": "Если код пришел и вы вошли, напишите, если нужна помощь со следующим шагом.",
        "steps": [
            "Проверьте, что номер телефона введен без ошибки и совпадает с номером, который вы указали при регистрации.",
            "Подождите 1-2 минуты и запросите код еще раз. Иногда SMS приходит не сразу.",
            "Проверьте связь, перезапустите телефон или отключите режим полета, затем снова запросите код.",
        ],
    },
    "account_inactive": {
        "intro": "Помогу проверить, почему аккаунт в Яндекс Про не активен.",
        "reply": "Разберем статус аккаунта по шагам.",
        "completed": "Если статус аккаунта обновился и можно продолжать, напишите, если нужна помощь дальше.",
        "steps": [
            "Закройте и заново откройте Яндекс Про, затем проверьте, изменился ли статус аккаунта.",
            "Убедитесь, что регистрация в парке уже завершена и вы входите по правильному номеру.",
            "Если статус не меняется, подготовьте короткое описание ошибки или текст на экране для менеджера.",
        ],
    },
    "go_online": {
        "intro": "Помогу выйти на линию в Яндекс Про.",
        "reply": "Идем по шагам, чтобы выйти на линию.",
        "completed": "Готово. Если линия открылась, можете начинать работу. Если что-то мешает принять заказ, напишите, что именно.",
        "steps": [
            "Откройте Яндекс Про и убедитесь, что вход выполнен под вашим рабочим номером.",
            "Проверьте, что в приложении заполнены обязательные шаги и нет блокирующих предупреждений.",
            "Нажмите кнопку выхода на линию и дождитесь, пока приложение покажет активный рабочий статус.",
        ],
    },
}


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
            self._record_system_trace(
                db,
                incoming_message.id,
                driver,
                state.value,
                incoming.text or "",
                intent="special_command",
                reply=command_reply,
                reasoning_summary="special_command",
            )
            return self._respond(db, driver, application, command_reply)

        pending_field = self._get_pending_field_edit(driver)
        if pending_field and state in {DialogueState.CONFIRM_DATA, DialogueState.YANDEX_ERROR}:
            return self._handle_pending_field_edit_value(
                db,
                driver,
                application,
                state,
                incoming.text or "",
                incoming_message.id,
                pending_field,
            )

        if state == DialogueState.COMPLETED:
            return self._handle_registered_driver_support(db, driver, application, incoming.text or "", incoming_message.id)

        if self._is_yandex_pro_followup_state(state):
            return self._handle_yandex_pro_followup(db, driver, application, state, incoming.text or "", incoming_message.id)

        if state == DialogueState.NEW:
            ai_result = self.ai.respond(state.value, incoming.text or "", driver)
            self._record_ai_trace(db, incoming_message.id, driver, state.value, incoming.text or "", ai_result)
            if ai_result.intent in {"faq", "help", "smalltalk"}:
                return self._respond(db, driver, application, self._format_new_state_assistant_reply(ai_result.reply))

            create_conversation_event(db, driver, "started_onboarding")
            set_application_status(db, application, "collecting_data")

            if ai_result.intent == "registration" and ai_result.extracted_fields:
                self._apply_extracted_fields(driver, ai_result.extracted_fields, db)
                next_state = DialogueState(ai_result.next_state or DialogueState.ASK_PHONE.value)
                update_driver_state(db, driver, next_state.value)
                set_application_status(db, application, _application_status_from_state(next_state))
                reply = "Здравствуйте. Начинаем регистрацию.\n\n" + PROMPTS[next_state]
                return self._respond(db, driver, application, reply)

            if ai_result.next_state == DialogueState.ASK_FULL_NAME.value:
                update_driver_state(db, driver, DialogueState.ASK_FULL_NAME.value)
                return self._respond(db, driver, application, ai_result.reply or PROMPTS[DialogueState.NEW])

            return self._respond(db, driver, application, ai_result.reply or PROMPTS[DialogueState.NEW])

        if state == DialogueState.ASK_EXECUTOR_TYPE:
            update_driver_state(db, driver, DialogueState.ASK_PHONE.value)
            set_application_status(db, application, "collecting_data")
            state = DialogueState.ASK_PHONE

        if state == DialogueState.DUPLICATE_REJECTED:
            return self._respond(db, driver, application, application.yandex_error or DUPLICATE_REJECTED_REPLY)

        ai_result = self.ai.respond(state.value, incoming.text or "", driver)
        self._record_ai_trace(db, incoming_message.id, driver, state.value, incoming.text or "", ai_result)
        if ai_result.intent in {"faq", "help", "smalltalk"}:
            return self._respond(db, driver, application, self._format_in_flow_assistant_reply(state, ai_result.reply))
        if ai_result.intent == "clarification":
            if ai_result.clear_suggested_clarification:
                self._clear_pending_car_model_suggestion(driver)
            elif ai_result.suggested_clarification_value:
                self._set_pending_car_model_suggestion(driver, ai_result.suggested_clarification_value)
            return self._respond(db, driver, application, self._format_in_flow_assistant_reply(state, ai_result.reply))
        if ai_result.intent == "field_edit":
            return self._handle_field_edit(db, driver, application, state, ai_result)
        if ai_result.intent == "correction":
            correction_state = DialogueState(ai_result.next_state or state.value)
            pending_target_field = self._correction_state_to_field_name(correction_state)
            if pending_target_field and state in {DialogueState.CONFIRM_DATA, DialogueState.YANDEX_ERROR}:
                self._set_pending_field_edit(driver, pending_target_field, state.value)
                create_conversation_event(
                    db,
                    driver,
                    "field_edit_requested",
                    {
                        "from_state": state.value,
                        "target_field": pending_target_field,
                        "message": incoming.text or "",
                    },
                )
                return self._respond(
                    db,
                    driver,
                    application,
                    f"Хорошо. Отправьте новое значение для поля «{self._field_label(pending_target_field)}» одним сообщением.",
                )
            update_driver_state(db, driver, correction_state.value)
            set_application_status(db, application, _application_status_from_state(correction_state))
            create_conversation_event(
                db,
                driver,
                "correction_requested",
                {"from_state": state.value, "to_state": correction_state.value, "message": incoming.text or ""},
            )
            return self._respond(db, driver, application, ai_result.reply or PROMPTS[correction_state])

        duplicate_reply = self._check_duplicate_constraints(db, driver, application, state, ai_result.extracted_fields)
        if duplicate_reply:
            return self._respond(db, driver, application, duplicate_reply)

        self._apply_extracted_fields(driver, ai_result.extracted_fields, db)
        if "model" in ai_result.extracted_fields:
            self._clear_pending_car_model_suggestion(driver)
        next_state = DialogueState(ai_result.next_state or state.value)

        if next_state == DialogueState.READY_TO_SEND_YANDEX:
            validation = self.yandex.validate_driver(driver)
            if validation["errors"]:
                retry_state = state if state == DialogueState.YANDEX_ERROR else DialogueState.CONFIRM_DATA
                update_driver_state(db, driver, retry_state.value)
                set_application_status(db, application, "confirming_data" if retry_state == DialogueState.CONFIRM_DATA else "yandex_error")
                issues = format_validation_errors_for_user(validation["errors"])
                return self._respond(
                    db,
                    driver,
                    application,
                    (
                        "Перед отправкой нужно исправить данные:\n\n"
                        f"{issues}\n\n"
                        f"{self._build_confirmation(driver, validation=validation)}"
                    ),
                )

            application.yandex_error = None
            db.add(application)
            update_driver_state(db, driver, DialogueState.SENDING_TO_YANDEX.value)
            set_application_status(db, application, "sending_to_yandex")
            self._respond(db, driver, application, PROMPTS[DialogueState.READY_TO_SEND_YANDEX])
            try:
                self.yandex.submit(db, driver, application)
                update_driver_state(db, driver, DialogueState.ASK_YANDEX_PRO_LOGIN.value)
                set_application_status(db, application, "sent_to_yandex", yandex_status="sent_to_yandex")
                create_conversation_event(db, driver, "submitted_to_yandex")
                create_conversation_event(db, driver, "yandex_pro_guidance_started")
                reply = self._build_yandex_pro_start_reply(driver)
            except YandexPartialSubmissionError as exc:
                update_driver_state(db, driver, DialogueState.YANDEX_ERROR.value)
                set_application_status(db, application, "yandex_error", yandex_status="partial_success", yandex_error=str(exc))
                driver.requires_attention = True
                db.add(driver)
                create_conversation_event(
                    db,
                    driver,
                    "submitted_to_yandex",
                    {
                        "status": "partial_success",
                        "stage": exc.stage,
                        "driver_id": exc.yandex_driver_id,
                        "vehicle_id": exc.yandex_vehicle_id,
                    },
                )
                create_conversation_event(
                    db,
                    driver,
                    "yandex_partial_success",
                    {
                        "error": str(exc),
                        "stage": exc.stage,
                        "driver_id": exc.yandex_driver_id,
                        "vehicle_id": exc.yandex_vehicle_id,
                    },
                )
                if exc.yandex_driver_id and not exc.yandex_vehicle_id:
                    reply = (
                        "Водитель уже создан в парке, но автомобиль не удалось добавить автоматически.\n\n"
                        f"{format_yandex_error_for_user(str(exc))}\n\n"
                        "Исправьте данные об автомобиле и напишите «Подтверждаю» — повторно создадим только машину."
                    )
                else:
                    reply = build_yandex_error_reply(str(exc))
            except Exception as exc:
                update_driver_state(db, driver, DialogueState.YANDEX_ERROR.value)
                set_application_status(db, application, "yandex_error", yandex_status="error", yandex_error=str(exc))
                driver.requires_attention = True
                db.add(driver)
                create_conversation_event(db, driver, "yandex_failed", {"error": str(exc)})
                reply = build_yandex_error_reply(str(exc))
            return self._respond(db, driver, application, reply)

        update_driver_state(db, driver, next_state.value)
        set_application_status(db, application, _application_status_from_state(next_state))
        reply = ai_result.reply or PROMPTS[next_state]
        if next_state == DialogueState.CONFIRM_DATA and ai_result.intent != "faq":
            reply = ai_result.reply or self._build_confirmation(driver, validation=self.yandex.validate_driver(driver))
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
        if self._is_yandex_pro_followup_state(state):
            driver.requires_attention = True
            db.add(driver)
            create_conversation_event(
                db,
                driver,
                "yandex_pro_attachment_received",
                {
                    "message_type": incoming.message_type,
                    "mime_type": incoming.mime_type,
                    "filename": incoming.filename,
                },
            )
            set_application_status(db, application, "awaiting_manager_review", yandex_status="driver_needs_help")
            return self._respond(
                db,
                driver,
                application,
                "Файл получил. Менеджер увидит его в чате и поможет дальше. Если вы уже вошли в Яндекс Про, напишите: Вошел.",
            )

        if state not in DOCUMENT_STATE_MAP:
            return self._respond(
                db,
                driver,
                application,
                "Сейчас ожидается текстовый ответ. Отправьте сообщение по текущему шагу.",
            )

        document_type = DOCUMENT_STATE_MAP[state]
        upsert_document(
            db,
            driver,
            document_type=document_type,
            file_url=None,
            google_drive_file_id=None,
            whatsapp_media_id=incoming.media_id,
            message_id=incoming_message_id,
            file_name=incoming.filename,
            mime_type=incoming.mime_type,
            storage_provider="whatsapp",
            storage_path=incoming.media_id,
            status="stored_in_whatsapp",
        )
        create_conversation_event(
            db,
            driver,
            "document_uploaded",
            {"document_type": document_type, "status": "stored_in_whatsapp"},
        )
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

    def _handle_field_edit(self, db: Session, driver: Driver, application, state: DialogueState, ai_result: AIResult) -> str:
        if state not in {DialogueState.CONFIRM_DATA, DialogueState.YANDEX_ERROR}:
            return self._respond(db, driver, application, self._format_in_flow_assistant_reply(state, ai_result.reply or PROMPTS[state]))

        if ai_result.validation_errors or not ai_result.normalized_fields:
            if ai_result.fallback_used:
                driver.fallback_count = (driver.fallback_count or 0) + 1
                db.add(driver)
            return self._respond(db, driver, application, ai_result.reply or "Не понял, что именно нужно изменить.")

        duplicate_state = state
        if "iin" in ai_result.normalized_fields:
            duplicate_state = DialogueState.ASK_IIN
        elif "plate_number" in ai_result.normalized_fields:
            duplicate_state = DialogueState.ASK_CAR_PLATE
        duplicate_reply = self._check_duplicate_constraints(db, driver, application, duplicate_state, ai_result.normalized_fields)
        if duplicate_reply:
            return self._respond(db, driver, application, duplicate_reply)

        changed_fields = self._apply_extracted_fields(driver, ai_result.normalized_fields, db, application=application, audit_action="field_corrected_by_user", actor_type="driver")
        create_conversation_event(
            db,
            driver,
            "field_corrected_by_user",
            {
                "target_field": ai_result.target_field,
                "changed_fields": changed_fields,
                "message": ai_result.new_value_raw,
            },
        )
        update_driver_state(db, driver, DialogueState.CONFIRM_DATA.value)
        set_application_status(db, application, "confirming_data", yandex_status="needs_resubmit")
        application.yandex_error = None
        db.add(application)
        return self._respond(
            db,
            driver,
            application,
            f"Готово, обновил поле «{self._field_label(ai_result.target_field)}». Проверьте данные еще раз.\n\n{self._build_confirmation(driver)}",
        )

    def _handle_pending_field_edit_value(
        self,
        db: Session,
        driver: Driver,
        application,
        state: DialogueState,
        message_text: str,
        incoming_message_id: int,
        target_field: str,
    ) -> str:
        target_state = self._field_name_to_correction_state(target_field)
        if target_state is None:
            self._clear_pending_field_edit(driver)
            return self._respond(db, driver, application, "Не удалось определить поле для исправления. Напишите, что именно нужно изменить.")

        ai_result = self.ai.respond(target_state.value, message_text, driver)
        ai_result.target_field = ai_result.target_field or target_field
        ai_result.reasoning_summary = ai_result.reasoning_summary or f"pending_field_edit:{target_field}"
        ai_result.suggested_next_action = ai_result.suggested_next_action or DialogueState.CONFIRM_DATA.value
        self._record_ai_trace(db, incoming_message_id, driver, state.value, message_text, ai_result)

        normalized_fields = ai_result.normalized_fields or ai_result.extracted_fields or {}
        if ai_result.validation_errors or not normalized_fields:
            reply = ai_result.reply or f"Отправьте корректное значение для поля «{self._field_label(target_field)}»."
            return self._respond(db, driver, application, reply)

        duplicate_state = state
        if "iin" in normalized_fields:
            duplicate_state = DialogueState.ASK_IIN
        elif "plate_number" in normalized_fields:
            duplicate_state = DialogueState.ASK_CAR_PLATE
        duplicate_reply = self._check_duplicate_constraints(db, driver, application, duplicate_state, normalized_fields)
        if duplicate_reply:
            return self._respond(db, driver, application, duplicate_reply)

        changed_fields = self._apply_extracted_fields(
            driver,
            normalized_fields,
            db,
            application=application,
            audit_action="field_corrected_by_user",
            actor_type="driver",
        )
        self._clear_pending_field_edit(driver)
        update_driver_state(db, driver, DialogueState.CONFIRM_DATA.value)
        set_application_status(db, application, "confirming_data", yandex_status="needs_resubmit")
        application.yandex_error = None
        db.add(application)
        create_conversation_event(
            db,
            driver,
            "field_corrected_by_user",
            {
                "target_field": target_field,
                "changed_fields": changed_fields,
                "message": message_text,
                "source": "pending_field_edit",
            },
        )
        return self._respond(
            db,
            driver,
            application,
            f"Готово, обновил поле «{self._field_label(target_field)}». Проверьте данные еще раз.\n\n{self._build_confirmation(driver)}",
        )

    def _record_ai_trace(
        self,
        db: Session,
        message_id: int,
        driver: Driver,
        state_before: str,
        input_text: str,
        ai_result: AIResult,
    ) -> None:
        incoming_message = next((message for message in driver.messages if message.id == message_id), None)
        if incoming_message is None:
            return
        if ai_result.fallback_used:
            driver.fallback_count = (driver.fallback_count or 0) + 1
            db.add(driver)
        upsert_message_ai_trace(
            db,
            message=incoming_message,
            driver_id=driver.id,
            state_before=state_before,
            input_text=input_text,
            provider=ai_result.provider,
            intent=ai_result.intent,
            confidence=ai_result.confidence,
            next_state=ai_result.next_state,
            reply_preview=ai_result.reply,
            extracted_fields_json=ai_result.extracted_fields or None,
            normalized_fields_json=ai_result.normalized_fields or ai_result.extracted_fields or None,
            reasoning_summary=ai_result.reasoning_summary,
            fallback_used=ai_result.fallback_used,
            fallback_reason=ai_result.fallback_reason,
            validation_errors_json=ai_result.validation_errors or None,
            suggested_next_action=ai_result.suggested_next_action,
            raw_decision_json=ai_result.raw_decision or None,
            final_decision_json=self._trace_payload(ai_result),
        )

    def _record_system_trace(
        self,
        db: Session,
        message_id: int,
        driver: Driver,
        state_before: str,
        input_text: str,
        *,
        intent: str,
        reply: str,
        reasoning_summary: str,
    ) -> None:
        incoming_message = next((message for message in driver.messages if message.id == message_id), None)
        if incoming_message is None:
            return
        upsert_message_ai_trace(
            db,
            message=incoming_message,
            driver_id=driver.id,
            state_before=state_before,
            input_text=input_text,
            provider="system",
            intent=intent,
            confidence=1.0,
            next_state=state_before,
            reply_preview=reply,
            extracted_fields_json=None,
            normalized_fields_json=None,
            reasoning_summary=reasoning_summary,
            fallback_used=False,
            fallback_reason=None,
            validation_errors_json=None,
            suggested_next_action=state_before,
            raw_decision_json={"intent": intent, "reply": reply},
            final_decision_json={"intent": intent, "reply": reply},
        )

    def _handle_support_flow(
        self,
        db: Session,
        driver: Driver,
        application,
        message_text: str,
        *,
        source_state: str,
    ) -> str | None:
        normalized = normalize_text_token(message_text)
        topic = _detect_support_topic(normalized, driver.active_support_topic)
        if not topic:
            return None

        flow = SUPPORT_FLOWS[topic]
        progress_words = {"сделал", "дальше", "готово", "получилось", "ок", "ok"}
        problem_words = {"не получается", "не вышло", "не работает", "ошибка", "не приходит", "не активен", "неактивен"}

        if driver.active_support_topic != topic:
            driver.active_support_topic = topic
            driver.active_support_step = "0"
            driver.support_context_json = {"source_state": source_state}
            db.add(driver)
            create_conversation_event(db, driver, "support_flow_started", {"topic": topic})
            return flow["intro"] + "\n\n" + self._support_step_text(topic, 0)

        current_step = int(driver.active_support_step or "0")
        if any(word in normalized for word in problem_words):
            driver.requires_attention = True
            driver.active_support_step = str(current_step)
            db.add(driver)
            set_application_status(db, application, "awaiting_manager_review", yandex_status="driver_needs_help")
            create_conversation_event(db, driver, "support_escalated_to_manager", {"topic": topic, "message": message_text})
            return "Понял. Передаю вопрос менеджеру. Пока менеджер проверяет, опишите коротко, на каком именно шаге возникла проблема."

        if any(word in normalized for word in progress_words):
            next_step = current_step + 1
            if next_step >= len(flow["steps"]):
                driver.active_support_topic = None
                driver.active_support_step = None
                driver.support_context_json = None
                db.add(driver)
                create_conversation_event(db, driver, "support_flow_completed", {"topic": topic})
                return flow["completed"]
            driver.active_support_step = str(next_step)
            db.add(driver)
            return self._support_step_text(topic, next_step)

        return flow["reply"] + "\n\n" + self._support_step_text(topic, current_step)

    def _support_step_text(self, topic: str, step_index: int) -> str:
        flow = SUPPORT_FLOWS[topic]
        step = flow["steps"][step_index]
        return f"Шаг {step_index + 1}: {step}\n\nКогда сделаете, напишите: сделал. Если не получается, напишите, что именно не выходит."

    def _is_yandex_pro_followup_state(self, state: DialogueState) -> bool:
        return state in {
            DialogueState.ASK_YANDEX_PRO_LOGIN,
            DialogueState.ASK_YANDEX_PRO_PROBLEM_DETAILS,
        }

    def _handle_yandex_pro_followup(
        self,
        db: Session,
        driver: Driver,
        application,
        state: DialogueState,
        message_text: str,
        incoming_message_id: int,
    ) -> str:
        normalized = normalize_text_token(message_text)
        ai_result = self.ai.respond(state.value, message_text, driver)
        self._record_ai_trace(db, incoming_message_id, driver, state.value, message_text, ai_result)

        support_reply = self._handle_support_flow(db, driver, application, message_text, source_state=state.value)
        if support_reply:
            return self._respond(db, driver, application, support_reply)

        if _looks_like_yandex_pro_success(normalized):
            update_driver_state(db, driver, DialogueState.COMPLETED.value)
            driver.requires_attention = False
            driver.active_support_topic = None
            driver.active_support_step = None
            driver.support_context_json = None
            db.add(driver)
            set_application_status(db, application, "completed", yandex_status="driver_login_confirmed")
            create_conversation_event(db, driver, "yandex_pro_login_confirmed")
            return self._respond(
                db,
                driver,
                application,
                (
                    "Отлично, вход в Яндекс Про зафиксировал. Вы уже можете выходить на линию.\n"
                    "Если нужен совет по работе, заявке или приложению, пишите сюда. Мы на связи.\n"
                    f"Адрес офиса: {self.settings.public_site_address}"
                ),
            )

        if _looks_like_yandex_pro_install_request(normalized):
            create_conversation_event(db, driver, "yandex_pro_install_help_sent")
            return self._respond(db, driver, application, self._build_yandex_pro_install_reply(driver))

        if _looks_like_yandex_pro_issue(normalized):
            update_driver_state(db, driver, DialogueState.ASK_YANDEX_PRO_PROBLEM_DETAILS.value)
            driver.requires_attention = True
            db.add(driver)
            set_application_status(db, application, "awaiting_manager_review", yandex_status="driver_needs_help")
            create_conversation_event(db, driver, "yandex_pro_help_requested", {"message": message_text})
            return self._respond(
                db,
                driver,
                application,
                "Понял. Напишите, что именно не получается при входе в Яндекс Про, и я передам это менеджеру. Если вы уже вошли, напишите: Вошел.",
            )

        if state == DialogueState.ASK_YANDEX_PRO_PROBLEM_DETAILS and message_text.strip():
            driver.requires_attention = True
            db.add(driver)
            set_application_status(db, application, "awaiting_manager_review", yandex_status="driver_needs_help")
            create_conversation_event(db, driver, "yandex_pro_problem_reported", {"message": message_text})
            return self._respond(
                db,
                driver,
                application,
                "Принял описание проблемы. После успешного входа напишите: Вошел.",
            )

        if ai_result.intent in {"faq", "help", "smalltalk", "clarification"} and ai_result.reply:
            return self._respond(db, driver, application, self._format_post_yandex_reply(state, ai_result.reply))

        return self._respond(db, driver, application, self._build_yandex_pro_start_reply(driver))

    def _handle_registered_driver_support(
        self,
        db: Session,
        driver: Driver,
        application,
        message_text: str,
        incoming_message_id: int,
    ) -> str:
        ai_result = self.ai.respond(DialogueState.COMPLETED.value, message_text, driver)
        self._record_ai_trace(db, incoming_message_id, driver, DialogueState.COMPLETED.value, message_text, ai_result)
        support_reply = self._handle_support_flow(
            db,
            driver,
            application,
            message_text,
            source_state=DialogueState.COMPLETED.value,
        )
        if support_reply:
            return self._respond(db, driver, application, support_reply)
        if ai_result.reply:
            return self._respond(db, driver, application, self._format_registered_driver_reply(ai_result.reply))
        return self._respond(
            db,
            driver,
            application,
            self._format_registered_driver_reply(
                "Регистрация уже завершена. Могу помочь по Яндекс Про, выходу на линию, условиям парка, выплатам, офису и дальнейшим шагам."
            ),
        )

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
            return STATUS_COLLECTING_DATA_TEMPLATE.format(state=current_step)
        if status == "duplicate_rejected":
            return application.yandex_error or STATUS_REPLIES["duplicate_rejected"]
        if status == "yandex_error":
            return build_yandex_error_reply(application.yandex_error)
        return STATUS_REPLIES.get(status, STATUS_FALLBACK_TEMPLATE.format(status=status))

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
        driver.active_support_topic = None
        driver.active_support_step = None
        driver.support_context_json = None
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

    def _apply_extracted_fields(
        self,
        driver: Driver,
        fields: dict[str, str],
        db: Session,
        *,
        application=None,
        audit_action: str | None = None,
        actor_type: str = "shared_admin",
    ) -> list[str]:
        vehicle = get_or_create_vehicle(db, driver)
        changed_fields: list[str] = []
        for key, value in fields.items():
            if key == "plate_number":
                value = normalize_plate_number(value)
            if key == "registration_certificate":
                value = normalize_registration_certificate(value)
            if key == "brand":
                resolved, _ = resolve_brand_input(value)
                value = resolved or normalize_car_brand(value)
            if key == "model":
                brand = vehicle.brand or fields.get("brand")
                if brand:
                    resolved, _ = resolve_model_input(brand, value)
                    value = resolved or normalize_car_model(value)
                else:
                    value = normalize_car_model(value)
            if hasattr(driver, key):
                old_value = getattr(driver, key)
                setattr(driver, key, value)
                if old_value != value:
                    changed_fields.append(key)
                    if audit_action:
                        create_audit_log(
                            db,
                            driver=driver,
                            application=application,
                            field_name=key,
                            old_value=str(old_value) if old_value is not None else None,
                            new_value=str(value) if value is not None else None,
                            action_type=audit_action,
                            actor_type=actor_type,
                        )
            elif hasattr(vehicle, key):
                old_value = getattr(vehicle, key)
                setattr(vehicle, key, value)
                if old_value != value:
                    changed_fields.append(key)
                    if audit_action:
                        create_audit_log(
                            db,
                            driver=driver,
                            application=application,
                            field_name=f"vehicle.{key}",
                            old_value=str(old_value) if old_value is not None else None,
                            new_value=str(value) if value is not None else None,
                            action_type=audit_action,
                            actor_type=actor_type,
                        )
        db.add(driver)
        db.add(vehicle)
        db.flush()
        return changed_fields

    def _build_confirmation(self, driver: Driver, validation: dict[str, list[str]] | None = None) -> str:
        vehicle = driver.vehicle
        if validation is None:
            validation = self.yandex.validate_driver(driver)
        issues_block = ""
        if validation.get("errors"):
            issues_block = (
                "\n\n⚠ Перед отправкой нужно исправить:\n"
                f"{format_validation_errors_for_user(validation['errors'])}\n"
            )
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
            f"Цвет: {vehicle.color if vehicle else '-'}\n"
            f"Номер СТС: {vehicle.registration_certificate if vehicle else '-'}"
            f"{issues_block}\n\n"
            'Если все верно, напишите "Подтверждаю". Если нужно исправить, напишите, что изменить.'
        )

    def _build_yandex_pro_start_reply(self, driver: Driver) -> str:
        contact_phone = driver.phone or driver.whatsapp_phone
        office_address = self.settings.public_site_address
        greeting_name = driver.first_name or driver.full_name or "водитель"
        return (
            f"{greeting_name}, спасибо, заявка уже отправлена в парк.\n\n"
            f"{YANDEX_PRO_START_TEMPLATE.format(phone=contact_phone)}\n\n"
            "Когда закончите вход, напишите: Вошел.\n"
            "Если приложение не скачалось, напишите: Не скачал.\n"
            "Если что-то не получается, просто опишите проблему - мы поможем дальше.\n\n"
            "После успешной регистрации приглашаем вас в офис. За регистрацию вы получите подарочный бокс: зарядку 3 в 1, держатель для телефона и салфетку.\n"
            "Если вы работаете в бизнес-классе, раз в неделю вам полагается блок воды.\n"
            "Наши постоянные водители могут бесплатно пользоваться сухим туманом.\n"
            f"Адрес офиса: {office_address}"
        )

    def _build_yandex_pro_install_reply(self, driver: Driver) -> str:
        contact_phone = driver.phone or driver.whatsapp_phone
        return YANDEX_PRO_INSTALL_TEMPLATE.format(phone=contact_phone)

    def _format_new_state_assistant_reply(self, base_reply: str) -> str:
        return (
            f"{base_reply}\n\n"
            "Если захотите сразу начать регистрацию, напишите ваше ФИО полностью."
        )

    def _format_in_flow_assistant_reply(self, state: DialogueState, base_reply: str) -> str:
        reminder = PROMPTS.get(state, "")
        if not reminder:
            return base_reply
        if base_reply.strip() == reminder.strip():
            return base_reply
        return f"{base_reply}\n\nТекущий шаг регистрации: {reminder}"

    def _format_post_yandex_reply(self, state: DialogueState, base_reply: str) -> str:
        if state == DialogueState.ASK_YANDEX_PRO_PROBLEM_DETAILS:
            reminder = "Если проблема уже решена и вы вошли в Яндекс Про, напишите: Вошел. Если нет, опишите, что именно не получается."
        else:
            reminder = (
                "Сейчас шаг после отправки заявки в парк: нужно зайти в Яндекс Про. "
                "Если уже вошли, напишите: Вошел. Если не получается, опишите проблему."
            )
        if base_reply.strip() == reminder.strip():
            return base_reply
        return f"{base_reply}\n\n{reminder}"

    def _format_registered_driver_reply(self, base_reply: str) -> str:
        tail = (
            "Если нужна помощь по Яндекс Про, выходу на линию, условиям парка, выплатам или офису, просто напишите вопрос. "
            f"Адрес офиса: {self.settings.public_site_address}"
        )
        if tail in base_reply:
            return base_reply
        return f"{base_reply}\n\n{tail}"

    def _get_pending_field_edit(self, driver: Driver) -> str | None:
        context = driver.support_context_json or {}
        pending = context.get("pending_field_edit")
        if isinstance(pending, dict):
            target_field = pending.get("target_field")
            if isinstance(target_field, str) and target_field:
                return target_field
        return None

    def _set_pending_field_edit(self, driver: Driver, target_field: str, source_state: str) -> None:
        context = dict(driver.support_context_json or {})
        context["pending_field_edit"] = {
            "target_field": target_field,
            "source_state": source_state,
            "requested_at": datetime.utcnow().isoformat(),
        }
        driver.support_context_json = context
        driver.updated_at = datetime.utcnow()

    def _clear_pending_field_edit(self, driver: Driver) -> None:
        context = dict(driver.support_context_json or {})
        context.pop("pending_field_edit", None)
        driver.support_context_json = context or None

    def _set_pending_car_model_suggestion(self, driver: Driver, suggested_model: str) -> None:
        context = dict(driver.support_context_json or {})
        context["pending_car_model_suggestion"] = suggested_model
        driver.support_context_json = context
        driver.updated_at = datetime.utcnow()

    def _clear_pending_car_model_suggestion(self, driver: Driver) -> None:
        context = dict(driver.support_context_json or {})
        if "pending_car_model_suggestion" not in context:
            return
        context.pop("pending_car_model_suggestion", None)
        driver.support_context_json = context or None
        driver.updated_at = datetime.utcnow()

    def _correction_state_to_field_name(self, state: DialogueState) -> str | None:
        mapping = {
            DialogueState.ASK_FULL_NAME: "full_name",
            DialogueState.ASK_PHONE: "phone",
            DialogueState.ASK_CITY: "city",
            DialogueState.ASK_ADDRESS: "address",
            DialogueState.ASK_IIN: "iin",
            DialogueState.ASK_BIRTH_DATE: "birth_date",
            DialogueState.ASK_DRIVING_EXPERIENCE_SINCE: "driving_experience_since",
            DialogueState.ASK_CAR_BRAND: "brand",
            DialogueState.ASK_CAR_MODEL: "model",
            DialogueState.ASK_CAR_YEAR: "year",
            DialogueState.ASK_CAR_PLATE: "plate_number",
            DialogueState.ASK_CAR_COLOR: "color",
            DialogueState.ASK_CAR_REGISTRATION_CERTIFICATE: "registration_certificate",
            DialogueState.ASK_DRIVER_LICENSE_NUMBER: "driver_license_number",
            DialogueState.ASK_DRIVER_LICENSE_ISSUE_DATE: "driver_license_issue_date",
            DialogueState.ASK_DRIVER_LICENSE_EXPIRES_AT: "driver_license_expires_at",
            DialogueState.ASK_EMPLOYMENT_TYPE: "employment_type",
            DialogueState.ASK_HIRED_AT: "hired_at",
            DialogueState.ASK_HEARING_IMPAIRED: "is_hearing_impaired",
        }
        return mapping.get(state)

    def _field_name_to_correction_state(self, field_name: str) -> DialogueState | None:
        mapping = {
            "full_name": DialogueState.ASK_FULL_NAME,
            "phone": DialogueState.ASK_PHONE,
            "city": DialogueState.ASK_CITY,
            "address": DialogueState.ASK_ADDRESS,
            "iin": DialogueState.ASK_IIN,
            "birth_date": DialogueState.ASK_BIRTH_DATE,
            "driving_experience_since": DialogueState.ASK_DRIVING_EXPERIENCE_SINCE,
            "brand": DialogueState.ASK_CAR_BRAND,
            "model": DialogueState.ASK_CAR_MODEL,
            "year": DialogueState.ASK_CAR_YEAR,
            "plate_number": DialogueState.ASK_CAR_PLATE,
            "color": DialogueState.ASK_CAR_COLOR,
            "registration_certificate": DialogueState.ASK_CAR_REGISTRATION_CERTIFICATE,
            "driver_license_number": DialogueState.ASK_DRIVER_LICENSE_NUMBER,
            "driver_license_issue_date": DialogueState.ASK_DRIVER_LICENSE_ISSUE_DATE,
            "driver_license_expires_at": DialogueState.ASK_DRIVER_LICENSE_EXPIRES_AT,
            "employment_type": DialogueState.ASK_EMPLOYMENT_TYPE,
            "hired_at": DialogueState.ASK_HIRED_AT,
            "is_hearing_impaired": DialogueState.ASK_HEARING_IMPAIRED,
        }
        return mapping.get(field_name)

    def _field_label(self, field_name: str | None) -> str:
        labels = {
            "full_name": "ФИО",
            "last_name": "фамилия",
            "first_name": "имя",
            "middle_name": "отчество",
            "phone": "телефон",
            "city": "город",
            "address": "адрес",
            "iin": "ИИН",
            "birth_date": "дата рождения",
            "driving_experience_since": "водительский стаж",
            "driver_license_number": "номер ВУ",
            "driver_license_issue_date": "дата выдачи ВУ",
            "driver_license_expires_at": "срок действия ВУ",
            "employment_type": "условие работы",
            "hired_at": "дата принятия",
            "is_hearing_impaired": "слабослышащий водитель",
            "brand": "марка авто",
            "model": "модель авто",
            "year": "год авто",
            "plate_number": "госномер",
            "color": "цвет авто",
            "registration_certificate": "номер СТС",
            "vin": "VIN",
            "service_class": "класс авто",
        }
        return labels.get(field_name or "", field_name or "поле")

    def _trace_payload(self, ai_result: AIResult) -> dict[str, object]:
        return {
            "reply": ai_result.reply,
            "intent": ai_result.intent,
            "next_state": ai_result.next_state,
            "confidence": ai_result.confidence,
            "target_field": ai_result.target_field,
            "new_value_raw": ai_result.new_value_raw,
            "extracted_fields": ai_result.extracted_fields,
            "normalized_fields": ai_result.normalized_fields,
            "reasoning_summary": ai_result.reasoning_summary,
            "fallback_used": ai_result.fallback_used,
            "fallback_reason": ai_result.fallback_reason,
            "validation_errors": ai_result.validation_errors,
            "suggested_next_action": ai_result.suggested_next_action,
            "provider": ai_result.provider,
        }

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


def _looks_like_yandex_pro_install_request(normalized: str) -> bool:
    contains = [
        "не скачал",
        "не установил",
        "как скачать",
        "где скачать",
        "скачать",
        "установить",
        "install",
        "download",
    ]
    return normalized in {"не скачал", "скачать"} or any(token in normalized for token in contains)


def _looks_like_yandex_pro_issue(normalized: str) -> bool:
    contains = [
        "ошиб",
        "не получается",
        "не могу войти",
        "не входит",
        "не заходит",
        "помощ",
        "help",
        "support",
        "смс не приходит",
        "код не приходит",
        "не приходит код",
    ]
    return normalized in {"ошибка", "помощь", "help"} or any(token in normalized for token in contains)


def _looks_like_yandex_pro_success(normalized: str) -> bool:
    return normalized in YANDEX_PRO_SUCCESS_KEYWORDS


def _detect_support_topic(normalized: str, active_topic: str | None) -> str | None:
    if active_topic and normalized in {"сделал", "дальше", "готово", "получилось", "ок", "ok"}:
        return active_topic
    if active_topic and any(
        token in normalized
        for token in {"не получается", "не вышло", "не работает", "ошибка", "не приходит", "не активен", "неактивен"}
    ):
        return active_topic

    topic_keywords = {
        "yandex_login": {"не могу войти", "не могу зайти", "войти в яндекс", "логин", "вход", "авторизация"},
        "yandex_sms": {"смс", "sms", "код не приходит", "не приходит код", "не пришел код", "не пришла смс"},
        "account_inactive": {"не активен", "неактивен", "аккаунт не активен", "профиль не активен"},
        "go_online": {"выйти на линию", "как выйти на линию", "на линию", "включить линию", "как начать работать"},
    }
    for topic, keywords in topic_keywords.items():
        if any(keyword in normalized for keyword in keywords):
            return topic
    return active_topic if active_topic and normalized in {"сделал", "дальше", "готово", "получилось"} else None


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
    if state in {
        DialogueState.SENT_TO_YANDEX,
        DialogueState.ASK_YANDEX_PRO_LOGIN,
        DialogueState.ASK_YANDEX_PRO_PROBLEM_DETAILS,
    }:
        return "sent_to_yandex"
    if state == DialogueState.YANDEX_ERROR:
        return "yandex_error"
    if state == DialogueState.DUPLICATE_REJECTED:
        return "duplicate_rejected"
    if state == DialogueState.COMPLETED:
        return "completed"
    return "collecting_data"
