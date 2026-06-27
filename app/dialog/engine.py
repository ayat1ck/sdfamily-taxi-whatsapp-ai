from datetime import datetime, timedelta

from sqlalchemy import desc, select
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
    OFFICE_HOURS,
    REGISTRATION_START_CTA,
    format_in_flow_reply,
)
from app.dialog.faq import classify_dialog_intent, resolve_faq_replies, SMALLTALK_REPLY, FALLBACK_MANAGER_REPLY
from app.dialog.states import DialogueState
from app.documents.extraction import DocumentExtractionService, normalize_extracted_fields
from app.documents.registration_flow import (
    DOCUMENT_TYPE_LABELS,
    MANUAL_DATA_ENTRY_REPLY,
    build_recognition_reply,
    expand_uploaded_document_types,
    is_expecting_data_document,
    is_registration_collecting_state,
    next_registration_state,
    resolve_document_type_for_upload,
    skip_data_documents_for_manual_entry,
)
from app.documents.service import upsert_document
from app.drivers.models import Driver
from app.drivers.service import (
    find_driver_by_iin,
    find_driver_by_phone,
    find_driver_by_whatsapp_phone,
    find_other_driver_by_iin,
    update_driver_state,
)
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
from app.messages.models import Message
from app.utils.logger import get_logger
from app.utils.text import repair_mojibake
from app.utils.validators import (
    looks_like_manual_data_entry,
    normalize_car_brand,
    normalize_car_model,
    normalize_plate_number,
    normalize_registration_certificate,
    normalize_text_token,
)
from app.vehicles.service import find_vehicle_by_plate_number, get_or_create_vehicle
from app.whatsapp.media import WhatsAppMediaClient
from app.whatsapp.parser import ParsedWhatsAppMessage

logger = get_logger(__name__)

SUPPORT_INTENTS = {
    "existing_driver_support",
    "human_operator",
    "payout_support",
    "tariff_support",
    "yandex_problem",
    "blocking_support",
    "rental_car_question",
    "courier_registration",
}

DUPLICATE_REJECTED_REPLY = (
    "Такой водитель уже зарегистрирован.\n\n"
    "Доступные действия:\n"
    "1. Стать самозанятым\n"
    "2. Изменить данные\n"
    "3. Сменить автомобиль\n"
    "4. Помощь со входом\n"
    "5. Связаться с менеджером"
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
        self.document_extractor = DocumentExtractionService()

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
        memory = self._load_conversation_memory(db, driver)
        self._remember_message_context(driver, incoming, memory)
        state = DialogueState(driver.state or DialogueState.NEW.value)

        pending_menu_reply = self._handle_pending_menu(
            db,
            driver,
            application,
            state,
            incoming.text or "",
            incoming_message.id,
        )
        if pending_menu_reply:
            return self._respond(db, driver, application, pending_menu_reply)

        active_flow_reply = self._handle_active_pending_action(
            db,
            driver,
            application,
            state,
            incoming.text or "",
            incoming_message.id,
        )
        if active_flow_reply:
            return self._respond(db, driver, application, active_flow_reply)

        if incoming.message_type == "text" and _looks_like_operator_request(normalize_text_token(incoming.text or "")):
            priority_reply = self._handle_priority_interrupts(
                db,
                driver,
                application,
                state,
                incoming.text or "",
                incoming_message.id,
            )
            if priority_reply:
                return self._respond(db, driver, application, priority_reply)

        if state == DialogueState.DUPLICATE_REJECTED and incoming.message_type in {"unsupported", "image", "document"}:
            return self._respond(db, driver, application, DUPLICATE_REJECTED_REPLY)

        if incoming.message_type == "unsupported":
            return self._respond(db, driver, application, "Поддерживаются только текст, изображение и документ.")

        support_menu_reply = self._handle_stateful_support_menu(
            db,
            driver,
            application,
            state,
            incoming.text or "",
            incoming_message.id,
        )
        if support_menu_reply:
            return self._respond(db, driver, application, support_menu_reply)

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

        priority_reply = self._handle_priority_interrupts(
            db,
            driver,
            application,
            state,
            incoming.text or "",
            incoming_message.id,
        )
        if priority_reply:
            return self._respond(db, driver, application, priority_reply)

        if incoming.message_type in {"image", "document"}:
            return self._handle_document(db, driver, application, incoming, incoming_message.id)

        if looks_like_manual_data_entry(incoming.text or "") and is_expecting_data_document(driver, state):
            reply = (
                "Для регистрации отправьте фото или PDF документа. "
                "По фото бот заполнит данные автоматически.\n\n"
                f"{PROMPTS[state]}"
            )
            return self._respond(db, driver, application, reply)

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
            if ai_result.confidence < 0.75 and ai_result.intent not in {"faq", "help", "smalltalk"}:
                ai_result.action = "ask_clarification"
            self._record_ai_trace(
                db,
                incoming_message.id,
                driver,
                state.value,
                incoming.text or "",
                ai_result,
                active_flow_after=state.value,
                decision_source="backend_router",
            )
            if _looks_like_registration_start_request(incoming.text or ""):
                create_conversation_event(db, driver, "started_onboarding")
                set_application_status(db, application, "collecting_data")
                update_driver_state(db, driver, DialogueState.ASK_FULL_NAME.value)
                return self._respond(
                    db,
                    driver,
                    application,
                    self._build_registration_start_reply("Да, можно зарегистрироваться в SD Family Taxi."),
                )

            if ai_result.intent in {"faq", "help", "smalltalk", *SUPPORT_INTENTS}:
                return self._respond(db, driver, application, self._format_new_state_assistant_reply(ai_result.reply))

            create_conversation_event(db, driver, "started_onboarding")
            set_application_status(db, application, "collecting_data")

            if ai_result.intent == "employment_type_change":
                return self._respond(db, driver, application, "Напишите, пожалуйста, какой тип сотрудничества нужен: штатный водитель, СМЗ или ИП.")

            if ai_result.intent == "registration" and ai_result.extracted_fields and ai_result.confidence >= 0.75:
                self._apply_extracted_fields(driver, ai_result.extracted_fields, db)
                next_state = DialogueState.ASK_PHONE
                update_driver_state(db, driver, next_state.value)
                set_application_status(db, application, _application_status_from_state(next_state))
                reply = "👋 Отлично! Начинаем регистрацию.\n\n" + PROMPTS[next_state]
                return self._respond(db, driver, application, reply)

            if (
                ai_result.suggested_next_action == DialogueState.ASK_FULL_NAME.value
                or ai_result.next_state == DialogueState.ASK_FULL_NAME.value
                or (ai_result.intent == "registration" and not ai_result.extracted_fields)
            ):
                update_driver_state(db, driver, DialogueState.ASK_FULL_NAME.value)
                return self._respond(
                    db,
                    driver,
                    application,
                    self._build_registration_start_reply(ai_result.reply),
                )

            if ai_result.action == "ask_clarification":
                return self._respond(db, driver, application, ai_result.reply or PROMPTS[DialogueState.NEW])

            return self._respond(db, driver, application, ai_result.reply or PROMPTS[DialogueState.NEW])

        if state == DialogueState.ASK_EXECUTOR_TYPE:
            update_driver_state(db, driver, DialogueState.ASK_PHONE.value)
            set_application_status(db, application, "collecting_data")
            state = DialogueState.ASK_PHONE

        if state == DialogueState.DUPLICATE_REJECTED:
            return self._respond(db, driver, application, DUPLICATE_REJECTED_REPLY)

        if self._is_active_flow(state) and _looks_like_current_step_help_request(incoming.text or ""):
            return self._respond(db, driver, application, self._step_instruction_reply(state))

        ai_result = self.ai.respond(state.value, incoming.text or "", driver)
        self._record_ai_trace(
            db,
            incoming_message.id,
            driver,
            state.value,
            incoming.text or "",
            ai_result,
            active_flow_after=state.value,
            decision_source="backend_router",
        )
        if self._is_active_flow(state) and ai_result.intent in {"faq", "help", "smalltalk"}:
            return self._respond(db, driver, application, self._repeat_current_question(state, ai_result.reply))
        if ai_result.intent in {"faq", "help", "smalltalk", *SUPPORT_INTENTS}:
            if self._is_active_flow(state) and not self._should_interrupt_active_flow(ai_result):
                return self._respond(db, driver, application, self._repeat_current_question(state, ai_result.reply))
            return self._respond(db, driver, application, ai_result.reply.strip())
        if ai_result.intent == "clarification":
            if ai_result.clear_suggested_clarification:
                self._clear_pending_car_model_suggestion(driver)
            elif ai_result.suggested_clarification_value:
                self._set_pending_car_model_suggestion(driver, ai_result.suggested_clarification_value)
            return self._respond(db, driver, application, self._format_in_flow_assistant_reply(state, ai_result.reply))
        if ai_result.intent == "employment_type_change":
            if ai_result.confidence < 0.75:
                return self._respond(db, driver, application, self._repeat_current_question(state, "Уточните, пожалуйста, хотите сменить тип сотрудничества на СМЗ, штатный формат или ИП?"))
            return self._respond(db, driver, application, self._repeat_current_question(state, "Понял. После завершения текущего шага помогу сменить тип сотрудничества."))
        if ai_result.intent == "field_edit":
            return self._handle_field_edit(db, driver, application, state, ai_result)
        if ai_result.intent == "correction":
            if ai_result.confidence < 0.75:
                return self._respond(db, driver, application, self._repeat_current_question(state, "Уточните, пожалуйста, какое именно поле нужно исправить."))
            correction_state = DialogueState(ai_result.suggested_next_action or ai_result.next_state or state.value)
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

        if ai_result.confidence < 0.75 and ai_result.intent == "registration":
            return self._respond(db, driver, application, self._repeat_current_question(state, ai_result.reply or "Уточните, пожалуйста, ответ на текущий вопрос."))

        self._apply_extracted_fields(driver, ai_result.extracted_fields, db)
        if "model" in ai_result.extracted_fields:
            self._clear_pending_car_model_suggestion(driver)
        next_state = next_text_state_after(state) if ai_result.extracted_fields else state

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
                        "Напишите правильную марку и модель автомобиля одним сообщением, например: Toyota Camry. "
                        "После исправления я снова попрошу проверить данные."
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
        next_state = next_registration_state(driver, driver.vehicle)
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
        media_context = self._classify_media_context(driver, state)
        support_context = self._get_support_context(driver)
        if support_context.get("mode") == "driver_profile_update":
            driver.requires_attention = True
            db.add(driver)
            create_conversation_event(
                db,
                driver,
                "profile_update_attachment_received",
                {
                    "message_type": incoming.message_type,
                    "mime_type": incoming.mime_type,
                    "filename": incoming.filename,
                    "field": support_context.get("field"),
                },
            )
            return self._respond(
                db,
                driver,
                application,
                "Файл получил. Использую его для обновления данных профиля. Если нужно, отправьте ещё одно фото или напишите уточнение текстом.",
            )
        if media_context == "correction_context":
            return self._respond(
                db,
                driver,
                application,
                "Файл получил. Для исправления данных напишите новое значение текстом или попросите менеджера.",
            )
        if media_context == "unknown_context":
            return self._respond(
                db,
                driver,
                application,
                "Фото получил. Сейчас оно не считается документом автоматически. Отправьте фото на шаге, где бот прямо просит документ, или напишите, чем помочь.",
            )
        if media_context in {"support_context", "existing_driver_support_context"}:
            driver.requires_attention = True
            db.add(driver)
            create_conversation_event(
                db,
                driver,
                "support_attachment_received",
                {
                    "media_context": media_context,
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

        vehicle = get_or_create_vehicle(db, driver)
        image_bytes: bytes | None = None
        mime_type = incoming.mime_type
        detected_type: str | None = None
        extraction = None

        if incoming.media_id:
            try:
                image_bytes, mime_type = self.media.fetch_media(incoming.media_id)
            except Exception as exc:
                logger.warning("Failed to download WhatsApp media %s: %s", incoming.media_id, exc)

        if self.document_extractor.is_enabled() and image_bytes:
            extraction = self.document_extractor.extract(
                image_bytes,
                mime_type=mime_type,
                expected_document_type=DOCUMENT_STATE_MAP.get(state, "unknown"),
            )
            if extraction.document_type and extraction.document_type != "unknown":
                detected_type = extraction.document_type

        document_type = resolve_document_type_for_upload(state, driver, detected_type=detected_type)
        if not document_type:
            create_conversation_event(
                db,
                driver,
                "document_type_not_determined",
                {
                    "state": state.value,
                    "message_type": incoming.message_type,
                    "mime_type": mime_type,
                    "media_id": incoming.media_id,
                    "extractor_enabled": self.document_extractor.is_enabled(),
                    "media_downloaded": bool(image_bytes),
                },
            )
            return self._respond(
                db,
                driver,
                application,
                (
                    "Фото получил, но не смог точно определить тип документа.\n\n"
                    "Отправьте одним фото один документ без бликов: водительское удостоверение, удостоверение личности или СТС. "
                    "Если есть PDF из eGov или Kaspi, тоже подойдет."
                ),
            )

        recognized: dict[str, str] = {}
        if image_bytes and self.document_extractor.is_enabled():
            if extraction is None or extraction.document_type != document_type:
                extraction = self.document_extractor.extract(
                    image_bytes,
                    mime_type=mime_type,
                    expected_document_type=document_type,
                )

        stored_document_types = expand_uploaded_document_types(
            document_type,
            mime_type=mime_type,
            contains_both_license_sides=bool(extraction and extraction.contains_both_license_sides),
            additional_document_types=extraction.additional_document_types if extraction else None,
        )
        for stored_type in stored_document_types:
            upsert_document(
                db,
                driver,
                document_type=stored_type,
                file_url=None,
                google_drive_file_id=None,
                whatsapp_media_id=incoming.media_id,
                message_id=incoming_message_id,
                file_name=incoming.filename,
                mime_type=mime_type,
                storage_provider="whatsapp",
                storage_path=incoming.media_id,
                status="stored_in_whatsapp",
            )
            create_conversation_event(
                db,
                driver,
                "document_uploaded",
                {"document_type": stored_type, "status": "stored_in_whatsapp", "source_mime_type": mime_type},
            )

        if image_bytes and self.document_extractor.is_enabled() and extraction is not None:
            fields, recognized = normalize_extracted_fields(extraction, document_type=document_type)
            if fields:
                if "iin" in fields:
                    duplicate_reply = self._check_duplicate_constraints(
                        db, driver, application, DialogueState.ASK_IIN, fields
                    )
                    if duplicate_reply:
                        return self._respond(db, driver, application, duplicate_reply)
                if "plate_number" in fields:
                    duplicate_reply = self._check_duplicate_constraints(
                        db, driver, application, DialogueState.ASK_CAR_PLATE, fields
                    )
                    if duplicate_reply:
                        return self._respond(db, driver, application, duplicate_reply)
                self._apply_extracted_fields(
                    driver,
                    fields,
                    db,
                    application=application,
                    audit_action="document_ocr_extracted",
                    actor_type="system",
                )
                create_conversation_event(
                    db,
                    driver,
                    "document_fields_extracted",
                    {"document_type": document_type, "fields": sorted(fields.keys())},
                )

        db.refresh(driver)
        vehicle = driver.vehicle or vehicle
        next_state = next_registration_state(driver, vehicle)
        update_driver_state(db, driver, next_state.value)
        set_application_status(db, application, _application_status_from_state(next_state))
        if next_state == DialogueState.CONFIRM_DATA:
            reply = self._build_confirmation(driver)
        else:
            reply = build_recognition_reply(stored_document_types, recognized, next_state)
        return self._respond(db, driver, application, reply)

    def _classify_media_context(self, driver: Driver, state: DialogueState) -> str:
        if driver.dialog_mode in {"manual", "paused", "closed"}:
            return "support_context"
        if state == DialogueState.COMPLETED:
            return "existing_driver_support_context"
        context = self._get_support_context(driver)
        if context.get("mode") == "driver_profile_update":
            return "support_context"
        if self._is_yandex_pro_followup_state(state) or driver.active_support_topic:
            return "support_context"
        if self._get_pending_field_edit(driver) or state in {DialogueState.CONFIRM_DATA, DialogueState.YANDEX_ERROR}:
            return "correction_context"
        if state == DialogueState.NEW or state in DOCUMENT_STATE_MAP or is_registration_collecting_state(state):
            return "registration_context"
        return "unknown_context"

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

    def _handle_manual_data_entry(
        self,
        db: Session,
        driver: Driver,
        application,
        state: DialogueState,
        message_text: str,
        incoming_message_id: int | None = None,
    ) -> str:
        skipped = skip_data_documents_for_manual_entry(db, driver)
        vehicle = get_or_create_vehicle(db, driver)
        next_state = next_registration_state(driver, vehicle)
        update_driver_state(db, driver, next_state.value)
        set_application_status(db, application, _application_status_from_state(next_state))
        create_conversation_event(
            db,
            driver,
            "manual_data_entry_selected",
            {"from_state": state.value, "skipped_documents": skipped},
        )
        next_prompt = (
            self._build_confirmation(driver)
            if next_state == DialogueState.CONFIRM_DATA
            else PROMPTS[next_state]
        )
        reply = f"{MANUAL_DATA_ENTRY_REPLY}\n\n📋 Следующий шаг:\n{next_prompt}"
        self._record_system_trace(
            db,
            incoming_message_id,
            driver,
            state.value,
            message_text,
            intent="manual_data_entry",
            reply=reply,
            reasoning_summary="manual_data_entry",
        )
        return self._respond(db, driver, application, reply)

    def _handle_field_edit(self, db: Session, driver: Driver, application, state: DialogueState, ai_result: AIResult) -> str:
        if state not in {DialogueState.CONFIRM_DATA, DialogueState.YANDEX_ERROR}:
            return self._respond(db, driver, application, self._format_in_flow_assistant_reply(state, ai_result.reply or PROMPTS[state]))

        if ai_result.validation_errors or not ai_result.normalized_fields:
            if ai_result.target_field and "missing_new_value" in (ai_result.validation_errors or []):
                self._set_pending_field_edit(driver, ai_result.target_field, state.value)
                create_conversation_event(
                    db,
                    driver,
                    "field_edit_requested",
                    {
                        "from_state": state.value,
                        "target_field": ai_result.target_field,
                        "message": ai_result.new_value_raw or "",
                    },
                )
            elif ai_result.fallback_used:
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
            f"✅ Готово, обновил поле «{self._field_label(ai_result.target_field)}». Проверьте данные ещё раз.\n\n{self._build_confirmation(driver)}",
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
        *,
        active_flow_after: str | None = None,
        decision_source: str = "ai_router",
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
            final_decision_json=self._trace_payload(
                ai_result,
                active_flow_before=state_before,
                active_flow_after=active_flow_after or ai_result.suggested_next_action or state_before,
                decision_source=decision_source,
            ),
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
        priority_intent: str | None = None,
        matched_rule: str | None = None,
    ) -> None:
        incoming_message = next((message for message in driver.messages if message.id == message_id), None)
        if incoming_message is None:
            return
        decision = {"intent": intent, "reply": reply}
        if priority_intent:
            decision["priority_intent"] = priority_intent
        if matched_rule:
            decision["matched_rule"] = matched_rule
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
            raw_decision_json=decision,
            final_decision_json=decision,
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
                    "🎉 Отлично, вы вошли в Яндекс Про! Можно выходить на линию.\n"
                    "💬 Если по работе появятся вопросы, пишите сюда.\n\n"
                    f"{self._build_office_bonus_block()}"
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
                "👌 Понял. Опишите, что не получается при входе в Яндекс Про — передам менеджеру.\n"
                "Уже вошли — напишите: Вошел",
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
                "✅ Принял. После успешного входа напишите: Вошел.",
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
            return f"🔄 Анкета сброшена. Начинаем заново.\n\n{REGISTRATION_START_CTA}"

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

    def _handle_priority_interrupts(
        self,
        db: Session,
        driver: Driver,
        application,
        state: DialogueState,
        message_text: str,
        incoming_message_id: int,
    ) -> str | None:
        normalized = normalize_text_token(message_text)
        if not normalized:
            return None

        detected_intent = classify_dialog_intent(message_text, current_state=state.value)
        if detected_intent == "human_operator" or _looks_like_operator_request(normalized):
            return self._activate_manual_mode(
                db,
                driver,
                application,
                state,
                message_text,
                incoming_message_id,
                intent="human_operator",
            )

        if _looks_like_self_employed_request(normalized):
            driver.requires_attention = True
            self._set_support_context(
                driver,
                {
                    "mode": "employment_type_change",
                    "menu": "smz_request",
                    "created_at": datetime.utcnow().isoformat(),
                    "expires_at": (datetime.utcnow() + timedelta(minutes=30)).isoformat(),
                },
            )
            db.add(driver)
            set_application_status(db, application, "awaiting_manager_review", yandex_status="self_employed_requested")
            create_conversation_event(db, driver, "self_employed_requested", {"message": message_text})
            self._record_system_trace(
                db,
                incoming_message_id,
                driver,
                state.value,
                message_text,
                intent="employment_type_change",
                reply="Принял. Передаю менеджеру заявку на перевод в статус самозанятого.",
                reasoning_summary="priority:employment_type_change",
                priority_intent="employment_type_change",
            )
            return "Принял. Передаю менеджеру заявку на перевод в статус самозанятого."

        if detected_intent == "existing_driver_support" or _looks_like_existing_driver_intent(normalized):
            self._set_support_context(
                driver,
                {
                    "mode": "existing_driver_support",
                    "menu": "existing_driver_main",
                    "created_at": datetime.utcnow().isoformat(),
                    "expires_at": (datetime.utcnow() + timedelta(minutes=30)).isoformat(),
                },
            )
            db.add(driver)
            create_conversation_event(db, driver, "existing_driver_support_menu_opened", {"message": message_text})
            reply = _existing_driver_options_reply()
            self._record_system_trace(
                db,
                incoming_message_id,
                driver,
                state.value,
                message_text,
                intent="existing_driver_support",
                reply=reply,
                reasoning_summary="priority:existing_driver_support",
                priority_intent="existing_driver_support",
            )
            return reply

        matched_rule = self._detect_driver_update_request(message_text)
        if matched_rule:
            return self._handle_driver_profile_update_entry(
                db,
                driver,
                application,
                state,
                message_text,
                incoming_message_id,
                matched_rule=matched_rule,
            )

        support_intent = _classify_priority_support_intent(normalized)
        if support_intent:
            reply = _priority_support_reply(support_intent)
            driver.requires_attention = True
            db.add(driver)
            set_application_status(db, application, "awaiting_manager_review", yandex_status=support_intent)
            create_conversation_event(db, driver, support_intent, {"message": message_text})
            self._record_system_trace(
                db,
                incoming_message_id,
                driver,
                state.value,
                message_text,
                intent=support_intent,
                reply=reply,
                reasoning_summary=f"priority:{support_intent}",
            )
            return reply

        if detected_intent == "smalltalk":
            reply = SMALLTALK_REPLY
            self._record_system_trace(
                db,
                incoming_message_id,
                driver,
                state.value,
                message_text,
                intent="smalltalk",
                reply=reply,
                reasoning_summary="priority:smalltalk",
            )
            return reply

        if detected_intent == "faq":
            reply = resolve_faq_replies(message_text, self.ai.knowledge_base, office_address=self.settings.public_site_address) or FALLBACK_MANAGER_REPLY
            self._record_system_trace(
                db,
                incoming_message_id,
                driver,
                state.value,
                message_text,
                intent="faq",
                reply=reply,
                reasoning_summary="priority:faq",
            )
            return reply

        if _looks_like_operator_request(normalized):
            driver.requires_attention = True
            driver.dialog_mode = "manual"
            driver.active_support_topic = None
            driver.active_support_step = None
            driver.support_context_json = {"human_required": True, "source_state": state.value}
            db.add(driver)
            set_application_status(db, application, "awaiting_manager_review", yandex_status="human_required")
            create_conversation_event(db, driver, "human_required", {"message": message_text})
            self._record_system_trace(
                db,
                incoming_message_id,
                driver,
                state.value,
                message_text,
                intent="human_required",
                reply="Ваш запрос передан менеджеру. Ожидайте ответа.",
                reasoning_summary="priority:human_required",
            )
            return "Ваш запрос передан менеджеру. Ожидайте ответа."

        if _looks_like_existing_driver_intent(normalized):
            self._record_system_trace(
                db,
                incoming_message_id,
                driver,
                state.value,
                message_text,
                intent="existing_driver",
                reply=_existing_driver_options_reply(),
                reasoning_summary="priority:existing_driver",
            )
            return _existing_driver_options_reply()

        if _looks_like_yandex_login_support(normalized):
            support_reply = self._handle_support_flow(db, driver, application, message_text, source_state=state.value)
            if support_reply:
                return support_reply
            driver.requires_attention = True
            db.add(driver)
            set_application_status(db, application, "awaiting_manager_review", yandex_status="driver_needs_help")
            create_conversation_event(db, driver, "yandex_login_help_requested", {"message": message_text})
            self._record_system_trace(
                db,
                incoming_message_id,
                driver,
                state.value,
                message_text,
                intent="yandex_login_support",
                reply="Передаю информацию менеджеру для проверки входа в Яндекс Про.",
                reasoning_summary="priority:yandex_login_support",
            )
            return "Передаю информацию менеджеру для проверки входа в Яндекс Про."

        if _looks_like_application_status_issue(normalized):
            reply = self._build_status_reply(driver, application)
            if not reply or application.status in {None, "", "collecting_data"}:
                reply = "Передаю информацию менеджеру для проверки заявки."
                driver.requires_attention = True
                db.add(driver)
                set_application_status(db, application, "awaiting_manager_review", yandex_status="status_check_required")
                create_conversation_event(db, driver, "application_status_check_requested", {"message": message_text})
            self._record_system_trace(
                db,
                incoming_message_id,
                driver,
                state.value,
                message_text,
                intent="application_status",
                reply=reply,
                reasoning_summary="priority:application_status",
            )
            return reply

        if _looks_like_tariff_issue(normalized):
            driver.requires_attention = True
            db.add(driver)
            set_application_status(db, application, "awaiting_manager_review", yandex_status="tariff_support_required")
            create_conversation_event(db, driver, "tariff_support_requested", {"message": message_text})
            self._record_system_trace(
                db,
                incoming_message_id,
                driver,
                state.value,
                message_text,
                intent="tariff_support",
                reply="Принял. Передаю вопрос менеджеру по тарифам и доступам.",
                reasoning_summary="priority:tariff_support",
            )
            return "Принял. Передаю вопрос менеджеру по тарифам и доступам."

        if _looks_like_data_change_request(normalized) and state not in {DialogueState.CONFIRM_DATA, DialogueState.YANDEX_ERROR}:
            driver.requires_attention = True
            db.add(driver)
            set_application_status(db, application, "awaiting_manager_review", yandex_status="data_change_requested")
            create_conversation_event(db, driver, "data_change_requested", {"message": message_text})
            self._record_system_trace(
                db,
                incoming_message_id,
                driver,
                state.value,
                message_text,
                intent="data_change_request",
                reply="Принял. Передаю менеджеру запрос на изменение данных водителя.",
                reasoning_summary="priority:data_change_request",
            )
            return "Принял. Передаю менеджеру запрос на изменение данных водителя."

        return None

    def _activate_manual_mode(
        self,
        db: Session,
        driver: Driver,
        application,
        state: DialogueState,
        message_text: str,
        incoming_message_id: int,
        *,
        intent: str,
    ) -> str:
        reply = "Ваш запрос передан менеджеру. Ожидайте ответа."
        driver.requires_attention = True
        driver.dialog_mode = "manual"
        driver.active_support_topic = None
        driver.active_support_step = None
        driver.support_context_json = {"human_required": True, "source_state": state.value}
        db.add(driver)
        set_application_status(db, application, "awaiting_manager_review", yandex_status="human_required")
        create_conversation_event(db, driver, "human_required", {"message": message_text})
        self._record_system_trace(
            db,
            incoming_message_id,
            driver,
            state.value,
            message_text,
            intent=intent,
            reply=reply,
            reasoning_summary=f"priority:{intent}",
        )
        return reply

    def _build_status_reply(self, driver: Driver, application) -> str:
        status = application.status or "collecting_data"
        if status == "collecting_data":
            current_step = driver.state or DialogueState.NEW.value
            return STATUS_COLLECTING_DATA_TEMPLATE.format(state=current_step)
        if status == "duplicate_rejected":
            return DUPLICATE_REJECTED_REPLY
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
        hearing_impaired = {
            "true": "да",
            "false": "нет",
        }.get((driver.is_hearing_impaired or "").strip().lower(), driver.is_hearing_impaired or "-")
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
            f"Слабослышащий водитель: {hearing_impaired}\n"
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
        greeting_name = driver.first_name or driver.full_name or "водитель"
        return (
            f"{greeting_name}, спасибо — заявка уже в парке! 🎉\n\n"
            f"{YANDEX_PRO_START_TEMPLATE.format(phone=contact_phone)}\n\n"
            f"{self._build_office_bonus_block()}"
        )

    def _build_yandex_pro_install_reply(self, driver: Driver) -> str:
        contact_phone = driver.phone or driver.whatsapp_phone
        return YANDEX_PRO_INSTALL_TEMPLATE.format(phone=contact_phone)

    def _format_new_state_assistant_reply(self, base_reply: str) -> str:
        return base_reply.strip()

    def _build_registration_start_reply(self, base_reply: str | None = None) -> str:
        reply = (base_reply or "👋 Отлично! Начинаем регистрацию.").strip()
        next_step = PROMPTS[DialogueState.ASK_FULL_NAME]
        if next_step not in reply:
            reply = f"{reply}\n\n{next_step}"
        return reply

    def _format_in_flow_assistant_reply(self, state: DialogueState, base_reply: str) -> str:
        return format_in_flow_reply(base_reply, state)

    def _format_post_yandex_reply(self, state: DialogueState, base_reply: str) -> str:
        if state == DialogueState.ASK_YANDEX_PRO_PROBLEM_DETAILS:
            reminder = "✅ Уже вошли в Яндекс Про — напишите: Вошел. Нет — опишите, что не получается."
        else:
            reminder = (
                "📱 Сейчас шаг: войти в Яндекс Про. "
                "Вошли — напишите: Вошел. Проблема — опишите в чат."
            )
        if base_reply.strip() == reminder.strip():
            return base_reply
        return f"{base_reply}\n\n{reminder}"

    def _format_registered_driver_reply(self, base_reply: str) -> str:
        return base_reply.strip()

    def _build_office_bonus_block(self) -> str:
        office_address = self.settings.public_site_address
        return (
            "🎁 После регистрации можно приехать в офис и забрать приветственный бонус.\n"
            "В бокс входят: зарядка 3 в 1, держатель для телефона, салфетка и тряпка.\n"
            "Для бизнес-класса дополнительно выдаем блок воды.\n"
            f"📍 Офис: {office_address}\n"
            f"{OFFICE_HOURS}"
        )

    def _get_support_context(self, driver: Driver) -> dict:
        context = driver.support_context_json or {}
        return context if isinstance(context, dict) else {}

    def _set_support_context(self, driver: Driver, context: dict | None) -> None:
        driver.support_context_json = context or None
        driver.updated_at = datetime.utcnow()

    def _clear_support_context(self, driver: Driver) -> None:
        driver.support_context_json = None
        driver.updated_at = datetime.utcnow()

    def _support_context_is_expired(self, context: dict) -> bool:
        expires_at = context.get("expires_at")
        if not isinstance(expires_at, str) or not expires_at:
            return False
        try:
            return datetime.fromisoformat(expires_at) <= datetime.utcnow()
        except ValueError:
            return False

    def _looks_like_driver_lookup_payload(self, message_text: str) -> bool:
        normalized = normalize_text_token(repair_mojibake(message_text))
        digits = "".join(ch for ch in normalized if ch.isdigit())
        if len(digits) in {10, 11, 12}:
            return True
        compact = "".join(ch for ch in normalized if ch.isalnum())
        if len(compact) == 12 and compact.isdigit():
            return True
        return any(marker in normalized for marker in ("iin", "ийн", "ииин"))

    def _find_existing_yandex_driver(
        self,
        db: Session,
        driver: Driver,
        application,
        lookup: str,
        *,
        source: str,
    ) -> Driver | None:
        try:
            profile = self.yandex.find_and_sync_existing_driver(db, driver, lookup)
        except Exception as exc:
            logger.warning("Yandex driver lookup failed for %s: %s", lookup, exc)
            application.yandex_error = f"Yandex lookup failed: {exc}"
            db.add(application)
            create_conversation_event(
                db,
                driver,
                "yandex_driver_lookup_failed",
                {"lookup": lookup, "source": source, "error": str(exc)},
            )
            return None
        if not profile:
            create_conversation_event(
                db,
                driver,
                "yandex_driver_lookup_empty",
                {"lookup": lookup, "source": source},
            )
            return None
        create_conversation_event(
            db,
            driver,
            "yandex_driver_lookup_found",
            {"lookup": lookup, "source": source, "driver_id": profile.id},
        )
        return profile

    def _build_driver_profile_card(self, driver: Driver) -> str:
        vehicle = driver.vehicle
        docs = []
        if getattr(vehicle, "registration_certificate", None):
            docs.append(f"СТС: {vehicle.registration_certificate}")
        if driver.driver_license_number:
            docs.append(f"ВУ: {driver.driver_license_number}")
        if driver.iin:
            docs.append(f"ИИН: {driver.iin}")
        vehicle_name = " ".join(part for part in [getattr(vehicle, "brand", None), getattr(vehicle, "model", None)] if part) or "не указан"
        return (
            "Нашёл ваш профиль:\n"
            f"ФИО: {driver.full_name or 'не указан'}\n"
            f"Телефон: {driver.phone or driver.whatsapp_phone or 'не указан'}\n"
            f"Авто: {vehicle_name} {getattr(vehicle, 'year', None) or 'не указан'}\n"
            f"Госномер: {getattr(vehicle, 'plate_number', None) or 'не указан'}\n"
            f"Документы: {', '.join(docs) if docs else 'не указаны'}\n"
            "Что хотите изменить?\n"
            "1. Автомобиль\n"
            "2. Госномер\n"
            "3. СТС/техпаспорт\n"
            "4. Водительское удостоверение\n"
            "5. Номер телефона\n"
            "6. Менеджер"
        )

    def _build_profile_update_menu(self, driver: Driver) -> str:
        base_card = self._build_driver_profile_card(driver).split("Что хотите изменить?")[0].rstrip()
        return base_card + (
            "\nЧто хотите изменить?\n"
            "1. ФИО\n"
            "2. Телефон\n"
            "3. Город/адрес\n"
            "4. Автомобиль\n"
            "5. Госномер\n"
            "6. СТС/техпаспорт\n"
            "7. Водительское удостоверение\n"
            "8. СМЗ/тип сотрудничества\n"
            "9. Менеджер"
        )

    def _detect_driver_update_request(self, message_text: str) -> str | None:
        normalized = normalize_text_token(repair_mojibake(message_text)).lower().strip(" ?!.,")
        markers = (
            "поменять машину",
            "хочу поменять машину",
            "поменять авто",
            "хочу поменять авто",
            "сменить машину",
            "сменить авто",
            "сменить автомобиль",
            "заменить машину",
            "заменить авто",
            "поменял машину",
            "купил новую машину",
            "обновить машину",
            "обновить авто",
            "изменить автомобиль",
            "изменить авто",
            "изменить данные авто",
            "изменить госномер",
            "поменять госномер",
            "обновить стс",
            "поменять стс",
            "поменять техпаспорт",
            "заменить техпаспорт",
            "поменять права",
            "заменить права",
            "обновить права",
            "водительское удостоверение поменять",
            "изменить номер телефона",
            "поменять телефон",
            "исправить фио",
            "поменять фио",
            "исправить имя",
            "данные неправильно",
            "изменить данные",
            "обновить документы",
            "поменять документы",
            "көлікті ауыстыру",
            "машина ауыстыру",
            "автокөлік ауыстыру",
            "техпаспорт ауыстыру",
            "құжат ауыстыру",
            "құжаттарды ауыстыру",
            "құқық ауыстыру",
            "номер ауыстыру",
            "деректерді өзгерту",
        )
        if any(marker in normalized for marker in markers):
            return next((marker for marker in markers if marker in normalized), "driver_update_request")
        return None

    def _load_conversation_memory(self, db: Session, driver: Driver) -> list[dict[str, object]]:
        rows = db.scalars(
            select(Message)
            .where(Message.driver_id == driver.id)
            .order_by(desc(Message.created_at), desc(Message.id))
            .limit(5)
        ).all()
        memory: list[dict[str, object]] = []
        for message in reversed(rows):
            memory.append(
                {
                    "id": message.id,
                    "direction": message.direction,
                    "sender_type": message.sender_type,
                    "message_type": message.message_type,
                    "text": message.text,
                    "created_at": message.created_at.isoformat() if message.created_at else None,
                }
            )
        return memory

    def _remember_message_context(self, driver: Driver, incoming: ParsedWhatsAppMessage, last_messages: list[dict[str, object]]) -> None:
        context = dict(driver.support_context_json or {})
        context["last_messages"] = last_messages
        if last_messages:
            last_bot = next((item for item in reversed(last_messages) if item.get("direction") == "outgoing"), None)
            if last_bot:
                context["last_bot_question"] = last_bot.get("text")
                context["last_intent"] = context.get("last_intent") or last_bot.get("message_type")
        if "pending_menu" not in context:
            context["pending_menu"] = context.get("menu")
        if incoming.message_type in {"image", "document"}:
            context["last_intent"] = "media"
        driver.support_context_json = context
        driver.updated_at = datetime.utcnow()

    def _handle_driver_profile_update_entry(
        self,
        db: Session,
        driver: Driver,
        application,
        state: DialogueState,
        message_text: str,
        incoming_message_id: int,
        *,
        matched_rule: str,
    ) -> str:
        profile = find_driver_by_whatsapp_phone(db, driver.whatsapp_phone)
        if profile:
            self._set_support_context(
                driver,
                {
                    "mode": "driver_profile_update",
                    "menu": "profile_update_menu",
                    "driver_id": profile.id,
                    "vehicle_id": getattr(profile.vehicle, "id", None),
                    "created_at": datetime.utcnow().isoformat(),
                    "expires_at": (datetime.utcnow() + timedelta(minutes=30)).isoformat(),
                },
            )
            db.add(driver)
            reply = self._build_profile_update_menu(profile)
            self._record_system_trace(
                db,
                incoming_message_id,
                driver,
                state.value,
                message_text,
                intent="driver_update_request",
                reply=reply,
                reasoning_summary="priority:driver_update_request",
                priority_intent="driver_update_request",
                matched_rule=matched_rule,
            )
            create_conversation_event(db, driver, "driver_profile_update_started", {"matched_rule": matched_rule, "driver_id": profile.id})
            return reply

        profile = self._find_existing_yandex_driver(
            db,
            driver,
            application,
            driver.whatsapp_phone,
            source="driver_update_request",
        )
        if profile:
            self._set_support_context(
                driver,
                {
                    "mode": "driver_profile_update",
                    "menu": "profile_update_menu",
                    "driver_id": profile.id,
                    "vehicle_id": getattr(profile.vehicle, "id", None),
                    "created_at": datetime.utcnow().isoformat(),
                    "expires_at": (datetime.utcnow() + timedelta(minutes=30)).isoformat(),
                },
            )
            db.add(driver)
            reply = self._build_profile_update_menu(profile)
            self._record_system_trace(
                db,
                incoming_message_id,
                driver,
                state.value,
                message_text,
                intent="driver_update_request",
                reply=reply,
                reasoning_summary="priority:driver_update_request_yandex_found",
                priority_intent="driver_update_request",
                matched_rule=matched_rule,
            )
            return reply

        self._set_support_context(
            driver,
            {
                "mode": "driver_lookup",
                "reason": "driver_update_request",
                "created_at": datetime.utcnow().isoformat(),
                "expires_at": (datetime.utcnow() + timedelta(minutes=30)).isoformat(),
            },
        )
        db.add(driver)
        reply = "Не нашёл профиль по этому WhatsApp-номеру. Напишите ИИН или номер телефона, на который зарегистрированы в Яндекс Про."
        self._record_system_trace(
            db,
            incoming_message_id,
            driver,
            state.value,
            message_text,
            intent="driver_update_request",
            reply=reply,
            reasoning_summary="priority:driver_update_request_no_profile",
            priority_intent="driver_update_request",
            matched_rule=matched_rule,
        )
        create_conversation_event(db, driver, "driver_profile_update_lookup_needed", {"matched_rule": matched_rule})
        return reply

    def _handle_stateful_support_menu(
        self,
        db: Session,
        driver: Driver,
        application,
        state: DialogueState,
        message_text: str,
        incoming_message_id: int,
    ) -> str | None:
        context = self._get_support_context(driver)
        if not context:
            return None
        if self._support_context_is_expired(context):
            self._clear_support_context(driver)
            db.add(driver)
            return None

        normalized = normalize_text_token(repair_mojibake(message_text)).strip()
        if context.get("mode") == "existing_driver_support" and context.get("menu") == "existing_driver_main":
            menu_map = {
                "1": "payout_support",
                "2": "yandex_problem",
                "3": "tariff_support",
                "4": "driver_update_request",
                "5": "human_operator",
            }
            choice = menu_map.get(normalized)
            if choice == "human_operator":
                self._set_support_context(
                    driver,
                    {
                        "mode": "manual",
                        "menu": "manual_mode",
                        "created_at": datetime.utcnow().isoformat(),
                        "expires_at": (datetime.utcnow() + timedelta(minutes=30)).isoformat(),
                    },
                )
                driver.dialog_mode = "manual"
                driver.requires_attention = True
                db.add(driver)
                set_application_status(db, application, "awaiting_manager_review", yandex_status="human_required")
                create_conversation_event(db, driver, "human_required", {"source": "existing_driver_support_menu"})
                self._record_system_trace(
                    db,
                    incoming_message_id,
                    driver,
                    state.value,
                    message_text,
                    intent="human_operator",
                    reply="Ваш запрос передан менеджеру. Ожидайте ответа.",
                    reasoning_summary="stateful_support_menu:human_operator",
                    priority_intent="human_operator",
                )
                return "Ваш запрос передан менеджеру. Ожидайте ответа."
            if choice == "driver_update_request":
                profile = find_driver_by_whatsapp_phone(db, driver.whatsapp_phone)
                if not profile:
                    profile = self._find_existing_yandex_driver(
                        db,
                        driver,
                        application,
                        driver.whatsapp_phone,
                        source="existing_driver_support_menu",
                    )
                if profile:
                    self._set_support_context(
                        driver,
                        {
                            "mode": "driver_profile_update",
                            "menu": "profile_update_menu",
                            "driver_id": profile.id,
                            "vehicle_id": getattr(profile.vehicle, "id", None),
                            "created_at": datetime.utcnow().isoformat(),
                            "expires_at": (datetime.utcnow() + timedelta(minutes=30)).isoformat(),
                        },
                    )
                    db.add(driver)
                    create_conversation_event(db, driver, "driver_update_profile_found", {"driver_id": profile.id})
                    return self._build_profile_update_menu(profile)
                self._set_support_context(
                    driver,
                    {
                        "mode": "driver_lookup",
                        "reason": "driver_update_request",
                        "created_at": datetime.utcnow().isoformat(),
                        "expires_at": (datetime.utcnow() + timedelta(minutes=30)).isoformat(),
                    },
                )
                db.add(driver)
                create_conversation_event(db, driver, "driver_update_profile_missing", {"source": "existing_driver_support_menu"})
                return "Не нашёл профиль по этому WhatsApp-номеру. Напишите ИИН или номер телефона, на который зарегистрированы в Яндекс Про."
            if choice:
                self._set_support_context(
                    driver,
                    {
                        "mode": choice,
                        "menu": choice,
                        "created_at": datetime.utcnow().isoformat(),
                        "expires_at": (datetime.utcnow() + timedelta(minutes=30)).isoformat(),
                    },
                )
                db.add(driver)
                create_conversation_event(db, driver, choice, {"source": "existing_driver_support_menu"})
                reply_map = {
                    "payout_support": _priority_support_reply("payout_support"),
                    "yandex_problem": _priority_support_reply("yandex_problem"),
                    "tariff_support": _priority_support_reply("tariff_support"),
                }
                return reply_map[choice]

        if context.get("mode") == "driver_lookup":
            if not self._looks_like_driver_lookup_payload(message_text):
                return None
            lookup_value = "".join(ch for ch in message_text if ch.isdigit())
            profile = (
                find_driver_by_phone(db, lookup_value)
                or find_driver_by_whatsapp_phone(db, lookup_value)
                or find_driver_by_iin(db, lookup_value)
            )
            if not profile:
                profile = self._find_existing_yandex_driver(
                    db,
                    driver,
                    application,
                    lookup_value,
                    source="driver_lookup",
                )
            if profile:
                self._set_support_context(
                    driver,
                    {
                        "mode": "driver_profile_update",
                        "menu": "profile_update_menu",
                        "driver_id": profile.id,
                        "vehicle_id": getattr(profile.vehicle, "id", None),
                        "created_at": datetime.utcnow().isoformat(),
                        "expires_at": (datetime.utcnow() + timedelta(minutes=30)).isoformat(),
                    },
                )
                db.add(driver)
                create_conversation_event(db, driver, "driver_update_profile_found", {"driver_id": profile.id, "lookup": lookup_value})
                return self._build_profile_update_menu(profile)
            driver.dialog_mode = "manual"
            driver.requires_attention = True
            db.add(driver)
            self._set_support_context(
                driver,
                {
                    "mode": "manual",
                    "menu": "manual_mode",
                    "created_at": datetime.utcnow().isoformat(),
                    "expires_at": (datetime.utcnow() + timedelta(minutes=30)).isoformat(),
                },
            )
            set_application_status(db, application, "awaiting_manager_review", yandex_status="driver_lookup_failed")
            create_conversation_event(db, driver, "driver_update_profile_missing", {"lookup": lookup_value})
            return "Не нашёл профиль. Передаю менеджеру."

        if context.get("mode") == "driver_profile_update" and context.get("menu") == "profile_update_menu":
            menu_map = {
                "1": "full_name",
                "2": "phone",
                "3": "location",
                "4": "vehicle",
                "5": "plate_number",
                "6": "registration_certificate",
                "7": "driver_license_number",
                "8": "employment_type",
                "9": "human_operator",
            }
            choice = menu_map.get(normalized)
            if not choice:
                return None
            self._set_support_context(
                driver,
                {
                    "mode": "driver_profile_update",
                    "menu": f"profile_update_{choice}",
                    "driver_id": context.get("driver_id"),
                    "vehicle_id": context.get("vehicle_id"),
                    "created_at": context.get("created_at") or datetime.utcnow().isoformat(),
                    "expires_at": (datetime.utcnow() + timedelta(minutes=30)).isoformat(),
                    "field": choice,
                    "active_flow": "driver_profile_update",
                    "pending_action": {
                        "full_name": "waiting_new_full_name",
                        "phone": "waiting_new_phone",
                        "location": "waiting_new_city_address",
                        "vehicle": "waiting_new_vehicle",
                        "plate_number": "waiting_new_plate",
                        "registration_certificate": "waiting_new_sts",
                        "driver_license_number": "waiting_new_driver_license",
                        "employment_type": "waiting_new_employment_type",
                        "human_operator": "waiting_manager",
                    }.get(choice),
                },
            )
            db.add(driver)
            if choice == "human_operator":
                driver.dialog_mode = "manual"
                driver.requires_attention = True
                set_application_status(db, application, "awaiting_manager_review", yandex_status="human_required")
                create_conversation_event(db, driver, "human_required", {"source": "profile_update_menu"})
                return "Ваш запрос передан менеджеру. Ожидайте ответа."
            create_conversation_event(db, driver, "driver_profile_update_requested", {"field": choice})
            prompt_map = {
                "full_name": "Напишите новые ФИО одним сообщением.",
                "phone": "Напишите новый номер телефона.",
                "location": "Напишите новый город или адрес.",
                "vehicle": "Пришлите данные по автомобилю или фото документов.",
                "plate_number": "Напишите новый госномер.",
                "registration_certificate": "Пришлите фото или номер СТС/техпаспорта.",
                "driver_license_number": "Пришлите фото или номер водительского удостоверения.",
                "employment_type": "Напишите новый тип сотрудничества или СМЗ.",
            }
            return prompt_map[choice]

        return None

    def _handle_pending_menu(
        self,
        db: Session,
        driver: Driver,
        application,
        state: DialogueState,
        message_text: str,
        incoming_message_id: int,
    ) -> str | None:
        context = self._get_support_context(driver)
        pending_menu = context.get("pending_menu") or context.get("menu")
        if not pending_menu:
            return self._handle_stateful_support_menu(db, driver, application, state, message_text, incoming_message_id)
        if pending_menu == "existing_driver_main":
            return self._handle_stateful_support_menu(db, driver, application, state, message_text, incoming_message_id)
        if pending_menu == "profile_update_menu" or context.get("mode") == "driver_profile_update":
            return self._handle_stateful_support_menu(db, driver, application, state, message_text, incoming_message_id)
        return None

    def _is_active_flow(self, state: DialogueState) -> bool:
        return state.value.startswith("ask_") or state in {DialogueState.CONFIRM_DATA, DialogueState.YANDEX_ERROR}

    def _repeat_current_question(self, state: DialogueState, base_reply: str) -> str:
        current_prompt = PROMPTS.get(state, "")
        if not current_prompt:
            return base_reply.strip()
        if not base_reply.strip():
            return current_prompt
        return f"{base_reply.strip()}\n\n{current_prompt}"

    def _step_instruction_reply(self, state: DialogueState) -> str:
        if state == DialogueState.ASK_FULL_NAME:
            return "Напишите ваше ФИО полностью одним сообщением. Например: Абай Аят Жаныбекулы."
        return format_in_flow_reply("", state)

    def _looks_like_cancel_request(self, message_text: str) -> bool:
        normalized = normalize_text_token(repair_mojibake(message_text))
        return normalized in {"отмена", "отменить", "стоп", "cancel", "cancel flow", "тоқтат", "бас тарту"}

    def _profile_update_prompt_for_action(self, pending_action: str | None) -> str:
        prompts = {
            "waiting_new_full_name": "Напишите новые ФИО одним сообщением.",
            "waiting_new_phone": "Напишите новый номер телефона.",
            "waiting_new_city_address": "Напишите новый город или адрес.",
            "waiting_new_vehicle": "Пришлите данные по автомобилю или фото документов.",
            "waiting_new_plate": "Напишите новый госномер.",
            "waiting_new_sts": "Пришлите фото или номер СТС/техпаспорта.",
            "waiting_new_driver_license": "Пришлите фото или номер водительского удостоверения.",
            "waiting_new_employment_type": "Напишите новый тип сотрудничества или СМЗ.",
        }
        return prompts.get(pending_action or "", "")

    def _handle_active_pending_action(
        self,
        db: Session,
        driver: Driver,
        application,
        state: DialogueState,
        message_text: str,
        incoming_message_id: int,
    ) -> str | None:
        context = self._get_support_context(driver)
        pending_action = context.get("pending_action")
        if not pending_action:
            return None

        if _looks_like_operator_request(normalize_text_token(message_text)):
            return self._activate_manual_mode(
                db,
                driver,
                application,
                state,
                message_text,
                incoming_message_id,
                intent="human_operator",
            )

        if self._looks_like_cancel_request(message_text):
            profile = find_driver_by_whatsapp_phone(db, driver.whatsapp_phone)
            if profile:
                self._set_support_context(
                    driver,
                    {
                        "mode": "driver_profile_update",
                        "menu": "profile_update_menu",
                        "driver_id": profile.id,
                        "vehicle_id": getattr(profile.vehicle, "id", None),
                        "created_at": context.get("created_at") or datetime.utcnow().isoformat(),
                        "expires_at": (datetime.utcnow() + timedelta(minutes=30)).isoformat(),
                    },
                )
                db.add(driver)
                return self._build_profile_update_menu(profile)
            self._clear_support_context(driver)
            db.add(driver)
            return _existing_driver_options_reply()

        if _looks_like_self_employed_request(normalize_text_token(message_text)):
            driver.requires_attention = True
            self._set_support_context(
                driver,
                {
                    "mode": "employment_type_change",
                    "menu": "smz_request",
                    "created_at": datetime.utcnow().isoformat(),
                    "expires_at": (datetime.utcnow() + timedelta(minutes=30)).isoformat(),
                },
            )
            db.add(driver)
            set_application_status(db, application, "awaiting_manager_review", yandex_status="self_employed_requested")
            create_conversation_event(db, driver, "self_employed_requested", {"message": message_text, "source": "pending_action"})
            self._record_system_trace(
                db,
                incoming_message_id,
                driver,
                state.value,
                message_text,
                intent="employment_type_change",
                reply="Принял. Передаю менеджеру заявку на перевод в статус самозанятого.",
                reasoning_summary="pending_action:employment_type_change",
                priority_intent="employment_type_change",
            )
            return "Принял. Передаю менеджеру заявку на перевод в статус самозанятого."

        faq_reply = resolve_faq_replies(message_text, self.ai.knowledge_base, office_address=self.settings.public_site_address)
        if faq_reply:
            prompt = self._profile_update_prompt_for_action(pending_action)
            return f"{faq_reply}\n\n{prompt}" if prompt else faq_reply
        if looks_like_greeting(message_text):
            prompt = self._profile_update_prompt_for_action(pending_action)
            return f"{SMALLTALK_REPLY}\n\n{prompt}" if prompt else SMALLTALK_REPLY
        return None

    def _should_interrupt_active_flow(self, ai_result: AIResult) -> bool:
        if ai_result.intent in {"human_operator"}:
            return True
        if ai_result.should_interrupt_current_flow:
            return True
        if ai_result.intent in {"existing_driver_support", "driver_profile_update", "employment_type_change"} and ai_result.confidence >= 0.75:
            return True
        if ai_result.intent in {"payout_support", "tariff_support", "yandex_problem", "blocking_support"} and ai_result.confidence >= 0.85:
            return True
        return False

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

    def _trace_payload(
        self,
        ai_result: AIResult,
        *,
        active_flow_before: str | None = None,
        active_flow_after: str | None = None,
        decision_source: str = "ai_router",
    ) -> dict[str, object]:
        return {
            "reply": ai_result.reply,
            "intent": ai_result.intent,
            "ai_intent": ai_result.intent,
            "ai_action": ai_result.action,
            "ai_field": ai_result.field,
            "next_state": ai_result.next_state,
            "confidence": ai_result.confidence,
            "target_field": ai_result.target_field,
            "extracted_value": ai_result.extracted_value,
            "reply_hint": ai_result.reply_hint,
            "should_interrupt_current_flow": ai_result.should_interrupt_current_flow,
            "new_value_raw": ai_result.new_value_raw,
            "extracted_fields": ai_result.extracted_fields,
            "normalized_fields": ai_result.normalized_fields,
            "reasoning_summary": ai_result.reasoning_summary,
            "fallback_used": ai_result.fallback_used,
            "fallback_reason": ai_result.fallback_reason,
            "validation_errors": ai_result.validation_errors,
            "suggested_next_action": ai_result.suggested_next_action,
            "provider": ai_result.provider,
            "active_flow_before": active_flow_before,
            "active_flow_after": active_flow_after,
            "decision_source": decision_source,
        }

    def _respond(self, db: Session, driver: Driver, application, reply: str) -> str:
        reply = repair_mojibake(reply)
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


def _looks_like_operator_request_legacy(normalized: str) -> bool:
    markers = (
        "оператор",
        "менеджер",
        "живой человек",
        "соедините",
        "позовите человека",
        "позовите менеджера",
        "техподдержка",
        "поддержка",
        "хочу поговорить с человеком",
    )
    return any(marker in normalized for marker in markers)


def _looks_like_operator_request(normalized: str) -> bool:
    exact = {
        "оператор",
        "менеджер",
        "техподдержка",
        "живой человек",
        "свяжите с менеджером",
        "адам оператор",
        "менеджер керек",
        "тірі адам",
        "қолдау керек",
    }
    if normalized.strip(" ?!.,") in exact:
        return True
    markers = (
        "operator",
        "manager",
        "support",
        "оператор",
        "менеджер",
        "техподдержка",
        "поддержка",
        "живой человек",
        "свяжите с менеджером",
        "соедините с менеджером",
        "позовите менеджера",
        "адам оператор",
        "менеджер керек",
        "тірі адам",
        "қолдау керек",
    )
    return any(marker in normalized for marker in markers)


def _classify_priority_support_intent(normalized: str) -> str | None:
    if _looks_like_payout_support(normalized):
        return "payout_support"
    if _looks_like_tariff_issue(normalized) or _looks_like_tariff_support(normalized):
        return "tariff_support"
    if _looks_like_yandex_login_support(normalized) or _looks_like_yandex_problem(normalized):
        return "yandex_problem"
    if _looks_like_blocking_support(normalized):
        return "blocking_support"
    if _looks_like_rental_car_question(normalized):
        return "rental_car_question"
    if _looks_like_courier_registration(normalized):
        return "courier_registration"
    return None


def _priority_support_reply(intent: str) -> str:
    replies = {
        "payout_support": "Принял вопрос по выплатам. Передаю менеджеру для проверки баланса, вывода или задержки выплаты.",
        "tariff_support": "Принял вопрос по тарифам. Передаю менеджеру, чтобы проверить доступы и настройки тарифов.",
        "yandex_problem": "Принял проблему с Яндекс Про. Передаю менеджеру для проверки входа, парка, приглашения или статуса аккаунта.",
        "blocking_support": "Принял вопрос по блокировке. Передаю менеджеру для проверки причины и дальнейших действий.",
        "rental_car_question": "Пока что аренды машин у таксопарка нет. Сейчас подключаем только водителей со своими автомобилями.",
        "courier_registration": "Принял вопрос по курьерской регистрации. Передаю менеджеру, чтобы отдельно проверить возможность подключения.",
    }
    return replies.get(intent, "Принял. Передаю вопрос менеджеру.")


def _looks_like_payout_support(normalized: str) -> bool:
    markers = (
        "выплата",
        "выплаты",
        "вывод",
        "деньги",
        "баланс",
        "моментальная выплата",
        "не пришли деньги",
        "ақша",
        "төлем",
        "төлем қашан",
        "ақша түспеді",
        "баланс шықпай тұр",
    )
    return any(marker in normalized for marker in markers)


def _looks_like_tariff_support(normalized: str) -> bool:
    markers = (
        "тариф",
        "комфорт",
        "бизнес",
        "межгород",
        "экспресс",
        "грузовой",
        "нет заказов",
        "заказы не идут",
        "тариф ашылмай тұр",
        "комфорт қосыңыз",
        "тапсырыс жоқ",
        "заказ жоқ",
    )
    return any(marker in normalized for marker in markers)


def _looks_like_yandex_problem(normalized: str) -> bool:
    markers = (
        "яндекс про",
        "не могу войти",
        "не заходит",
        "парк не вижу",
        "нет парка",
        "не пришло приглашение",
        "аккаунт не активен",
        "код не приходит",
        "смс не приходит",
        "yandex pro",
        "яндекс кірмей тұр",
        "парк көрінбей тұр",
        "код келмеді",
        "смс келмеді",
        "аккаунт ашылмай тұр",
    )
    return any(marker in normalized for marker in markers)


def _looks_like_blocking_support(normalized: str) -> bool:
    markers = (
        "заблокировали",
        "блокировка",
        "заблокирован",
        "доступ закрыт",
        "аккаунт заблокирован",
        "профиль заблокирован",
        "бұғатталды",
        "аккаунт бұғат",
        "кіре алмаймын блок",
    )
    return any(marker in normalized for marker in markers)


def _looks_like_rental_car_question(normalized: str) -> bool:
    markers = (
        "аренда авто",
        "арендная машина",
        "машина в аренду",
        "есть авто",
        "нужна машина",
        "таксопарк дает машину",
        "көлік жалға",
        "аренда көлік",
        "машина керек",
        "көлік бар ма",
    )
    return any(marker in normalized for marker in markers)


def _looks_like_courier_registration(normalized: str) -> bool:
    markers = (
        "курьер",
        "курьером",
        "доставка",
        "еда",
        "хочу курьером",
        "курьерская регистрация",
        "курьер болып",
        "жеткізу",
        "доставкаға тіркел",
        "курьер тіркеу",
    )
    return any(marker in normalized for marker in markers)


def _looks_like_existing_driver_intent(normalized: str) -> bool:
    plain = normalize_text_token(repair_mojibake(normalized)).strip(" ?!.,")
    strong_markers = (
        "я уже подключен",
        "я подключен уже",
        "я уже зарегистрирован",
        "я уже водитель",
        "я уже работаю",
        "я в вашем парке",
        "я есть в системе",
        "уже регался",
        "мен тіркелгенмін",
        "мен жүргізушімін",
    )
    if any(marker in plain for marker in strong_markers):
        return True
    readable_markers = (
        "я уже зарегистрирован",
        "я уже водитель",
        "я работаю у вас",
        "я есть в базе",
        "я подключен",
        "я в вашем парке",
        "уже зарегистрирован",
        "уже водитель",
        "мен тіркелгенмін",
        "мен жүргізушімін",
        "сіздің парктемін",
        "паркке қосылғанмын",
    )
    if any(marker in normalized for marker in readable_markers):
        return True
    markers = (
        "я уже зарегистрирован",
        "я уже водитель",
        "я работаю у вас",
        "я есть в базе",
        "я подключен",
        "я в вашем парке",
    )
    if not any(marker in normalized for marker in markers):
        return False
    if _looks_like_self_employed_request(normalized):
        return False
    if _looks_like_yandex_login_support(normalized):
        return False
    if _looks_like_application_status_issue(normalized):
        return False
    if _looks_like_tariff_issue(normalized):
        return False
    if _looks_like_data_change_request(normalized):
        return False
    return True


def _looks_like_application_status_issue(normalized: str) -> bool:
    markers = (
        "когда будет готово",
        "где моя заявка",
        "меня нет в парке",
        "не вижу парк",
        "не пришло приглашение",
        "не отображается парк",
        "заявка не обработана",
    )
    return any(marker in normalized for marker in markers)


def _looks_like_yandex_login_support(normalized: str) -> bool:
    markers = (
        "не могу войти",
        "не заходит",
        "ошибка входа",
        "нет парка",
        "не вижу парк",
        "нет аккаунта",
        "не отображается таксопарк",
    )
    return any(marker in normalized for marker in markers)


def _looks_like_tariff_issue(normalized: str) -> bool:
    markers = (
        "не могу отключить тариф",
        "включите комфорт",
        "включите межгород",
        "включите экспресс",
        "подключите тариф",
        "почему нет комфорта",
        "почему нет заказов",
    )
    return any(marker in normalized for marker in markers)


def _looks_like_data_change_request(normalized: str) -> bool:
    markers = (
        "поменял машину",
        "сменил номер",
        "изменить данные",
        "поменять иин",
        "изменить права",
        "обновить документы",
    )
    return any(marker in normalized for marker in markers)


def _looks_like_self_employed_request(normalized: str) -> bool:
    markers = (
        "хочу стать самозанятым",
        "сделайте смз",
        "парковый самозанятый",
    )
    return any(marker in normalized for marker in markers)


def _existing_driver_options_reply() -> str:
    return (
        "Понял, вы уже подключены. Что нужно сделать?\n"
        "1. Вывод денег\n"
        "2. Вход в Яндекс Про\n"
        "3. Тарифы\n"
        "4. Изменить авто/документы\n"
        "5. Менеджер"
    )


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


def _looks_like_registration_start_request(text: str) -> bool:
    normalized = normalize_text_token(text)
    if not normalized:
        return False
    if any(marker in normalized for marker in ("уже подключ", "уже зарегистр", "подключен уже", "подключён уже")):
        return False
    start_markers = (
        "зарегистр",
        "регистрац",
        "подключ",
        "тирк",
        "тірк",
        "тіркел",
        "паркка",
    )
    intent_markers = (
        "можно",
        "хочу",
        "надо",
        "нужно",
        "давайте",
        "начать",
        "начина",
        "пройти",
        "деген",
        "едим",
    )
    if any(marker in normalized for marker in start_markers):
        return True
    return "таксопарк" in normalized and any(marker in normalized for marker in intent_markers)


def _looks_like_current_step_help_request(text: str) -> bool:
    normalized = normalize_text_token(text)
    compact = normalized.strip(" ?!.")
    if not compact:
        return False
    if set(compact) <= {"?"}:
        return True
    markers = (
        "что делать",
        "что дальше",
        "что писать",
        "что написать",
        "какой ответ",
        "какой ответ писать",
        "как ответить",
        "не понял",
        "не понимаю",
        "объясни",
        "поясни",
    )
    return any(marker in normalized for marker in markers)


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
        "account_inactive": {
            "не активен",
            "неактивен",
            "аккаунт не активен",
            "профиль не активен",
            "с нуля",
            "обратно кинули",
            "заново зарегистрироваться",
            "обратно на регистрацию",
            "кинули на регистрацию",
        },
        "go_online": {
            "выйти на линию",
            "как выйти на линию",
            "на линию",
            "включить линию",
            "как начать работать",
            "нет заказов",
            "ни одного заказа",
            "не одного заказа",
            "заказов не дали",
            "час посидел",
            "долго без заказов",
            "нет заказа",
        },
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
DUPLICATE_REJECTED_REPLY = (
    "Такой водитель уже зарегистрирован.\n\n"
    "Доступные действия:\n"
    "1. Стать самозанятым\n"
    "2. Изменить данные\n"
    "3. Сменить автомобиль\n"
    "4. Помощь со входом\n"
    "5. Связаться с менеджером"
)


def _existing_driver_options_reply() -> str:
    return (
        "Понял, вы уже подключены. Что нужно сделать?\n"
        "1. Вывод денег\n"
        "2. Вход в Яндекс Про\n"
        "3. Тарифы\n"
        "4. Изменить авто/документы\n"
        "5. Менеджер"
    )
