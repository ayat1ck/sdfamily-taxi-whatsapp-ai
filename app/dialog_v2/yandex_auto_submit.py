from __future__ import annotations

from copy import deepcopy
from datetime import datetime

from sqlalchemy.orm.attributes import flag_modified

from app.applications.service import set_application_status
from app.config import get_settings
from app.dialog_v2.event_bus import EventBus
from app.dialog_v2.response import StructuredReply
from app.dialog_v2.states import DialogV2State
from app.integrations.yandex.client import YandexPartialSubmissionError
from app.integrations.yandex.service import YandexSubmissionService
from app.utils.text import repair_mojibake
from app.vehicles.service import get_or_create_vehicle


SUCCESS_TEXT = (
    "\u0413\u043e\u0442\u043e\u0432\u043e. \u0410\u043d\u043a\u0435\u0442\u0430 \u0430\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u0438 "
    "\u043e\u0442\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0430 \u0432 \u042f\u043d\u0434\u0435\u043a\u0441. "
    "\u0414\u0430\u043b\u044c\u0448\u0435 \u043c\u044b \u043f\u0440\u043e\u0432\u0435\u0440\u0438\u043c \u0441\u0442\u0430\u0442\u0443\u0441 "
    "\u0438 \u043f\u043e\u0434\u0441\u043a\u0430\u0436\u0435\u043c \u0441\u043b\u0435\u0434\u0443\u044e\u0449\u0438\u0439 \u0448\u0430\u0433."
)

FAILURE_PREFIX = (
    "\u0410\u043d\u043a\u0435\u0442\u0430 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u0430, "
    "\u043d\u043e \u0430\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u0438 \u043e\u0442\u043f\u0440\u0430\u0432\u0438\u0442\u044c "
    "\u0432 \u042f\u043d\u0434\u0435\u043a\u0441 \u043d\u0435 \u043f\u043e\u043b\u0443\u0447\u0438\u043b\u043e\u0441\u044c."
)

CONFIRM_RETRY_TEXT = (
    "\u0418\u0441\u043f\u0440\u0430\u0432\u044c\u0442\u0435 \u0434\u0430\u043d\u043d\u044b\u0435 \u0438 "
    "\u043d\u0430\u043f\u0438\u0448\u0438\u0442\u0435 \"\u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0430\u044e\" "
    "\u0434\u043b\u044f \u043f\u043e\u0432\u0442\u043e\u0440\u043d\u043e\u0439 \u043e\u0442\u043f\u0440\u0430\u0432\u043a\u0438 "
    "\u0438\u043b\u0438 \"\u043c\u0435\u043d\u0435\u0434\u0436\u0435\u0440\", \u0435\u0441\u043b\u0438 \u043d\u0443\u0436\u043d\u0430 "
    "\u043f\u043e\u043c\u043e\u0449\u044c."
)

YANDEX_NOT_CONFIGURED_TEXT = (
    "\u041f\u0440\u0438\u043d\u044f\u0442\u043e. \u0410\u043d\u043a\u0435\u0442\u0430 \u0433\u043e\u0442\u043e\u0432\u0430 "
    "\u043a \u043e\u0442\u043f\u0440\u0430\u0432\u043a\u0435 \u0432 \u042f\u043d\u0434\u0435\u043a\u0441, "
    "\u043d\u043e \u0430\u0432\u0442\u043e\u043e\u0442\u043f\u0440\u0430\u0432\u043a\u0430 \u0435\u0449\u0451 "
    "\u043d\u0435 \u043d\u0430\u0441\u0442\u0440\u043e\u0435\u043d\u0430: \u043d\u0435 \u0445\u0432\u0430\u0442\u0430\u0435\u0442 "
    "Yandex ENV. \u0414\u0430\u043d\u043d\u044b\u0435 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u044b."
)


class DialogV2YandexAutoSubmit:
    def __init__(self) -> None:
        self.yandex = YandexSubmissionService()
        self.bus = EventBus()

    def submit(self, db, driver, application, draft: dict | None = None) -> StructuredReply:
        draft = draft or self._draft(driver) or {}
        self.sync_draft_to_backend(db, driver, draft)
        driver.state = DialogV2State.READY_TO_SEND_YANDEX
        if self._missing_yandex_config():
            set_application_status(db, application, "ready_to_send_yandex", yandex_status="not_configured")
            self.bus.emit(db, driver, "registration_confirmed", {"draft": draft})
            self.bus.emit(db, driver, "yandex_auto_submit_skipped", {"reason": "missing_yandex_config"})
            return StructuredReply(
                text=YANDEX_NOT_CONFIGURED_TEXT,
                next_flow=DialogV2State.READY_TO_SEND_YANDEX,
                flow_state=DialogV2State.READY_TO_SEND_YANDEX,
                metadata={
                    "intent": "confirmation",
                    "draft_ready_for_yandex": True,
                    "yandex_auto_submit": False,
                    "yandex_submit_status": "not_configured",
                },
            )
        set_application_status(db, application, "sending_to_yandex", yandex_status="sending")
        self.bus.emit(db, driver, "registration_confirmed", {"draft": draft})

        try:
            self.yandex.submit(db, driver, application)
        except YandexPartialSubmissionError as exc:
            self.bus.emit(
                db,
                driver,
                "yandex_partial_success",
                {"error": str(exc), "stage": exc.stage, "driver_id": exc.yandex_driver_id, "vehicle_id": exc.yandex_vehicle_id},
            )
            return self._failure_reply(driver, application, exc)
        except Exception as exc:
            set_application_status(db, application, "yandex_error", yandex_status="error", yandex_error=str(exc))
            self.bus.emit(db, driver, "yandex_failed", {"error": str(exc)})
            return self._failure_reply(driver, application, exc)

        driver.state = DialogV2State.COMPLETED
        self.bus.emit(
            db,
            driver,
            "submitted_to_yandex",
            {"application_id": application.id, "yandex_status": application.yandex_status},
        )
        return StructuredReply(
            text=SUCCESS_TEXT,
            next_flow=DialogV2State.COMPLETED,
            flow_state=DialogV2State.COMPLETED,
            metadata={
                "intent": "confirmation",
                "draft_ready_for_yandex": True,
                "yandex_auto_submit": True,
                "yandex_submit_status": "success",
            },
        )

    def sync_draft_to_backend(self, db, driver, draft: dict) -> None:
        driver_fields = draft.get("driver") or {}
        vehicle_fields = draft.get("vehicle") or {}
        extra_fields = draft.get("extra_fields") or {}

        for field in (
            "full_name",
            "iin",
            "birth_date",
            "driving_experience_since",
            "driver_license_number",
            "driver_license_issue_date",
            "driver_license_expires_at",
            "phone",
            "city",
            "address",
            "employment_type",
            "hired_at",
            "is_hearing_impaired",
        ):
            value = driver_fields.get(field)
            if value:
                setattr(driver, field, self._clean(value))

        if not driver.phone:
            driver.phone = driver.whatsapp_phone
        if driver.city and not driver.address:
            driver.address = driver.city
        if not driver.employment_type:
            driver.employment_type = "\u0441\u0430\u043c\u043e\u0437\u0430\u043d\u044f\u0442\u044b\u0439"
        if not driver.hired_at:
            driver.hired_at = datetime.utcnow().date().isoformat()
        if not driver.is_hearing_impaired:
            driver.is_hearing_impaired = "false"

        self._sync_name_parts(driver, driver_fields, extra_fields)

        vehicle = get_or_create_vehicle(db, driver)
        for field in ("brand", "model", "year", "plate_number", "color", "registration_certificate", "vin"):
            value = vehicle_fields.get(field)
            if value:
                setattr(vehicle, field, self._clean(value))

        self._store_synced_draft(driver, draft)
        db.add(driver)
        db.add(vehicle)
        db.flush()

    def _sync_name_parts(self, driver, driver_fields: dict, extra_fields: dict) -> None:
        for field in ("last_name", "first_name", "middle_name"):
            value = driver_fields.get(field) or extra_fields.get(field)
            if value:
                setattr(driver, field, self._clean(value))

        if driver.last_name and driver.first_name:
            return

        parts = [part for part in self._clean(driver.full_name).split() if part]
        if not parts:
            return
        if not driver.last_name:
            driver.last_name = parts[0]
        if len(parts) > 1 and not driver.first_name:
            driver.first_name = parts[1]
        if len(parts) > 2 and not driver.middle_name:
            driver.middle_name = " ".join(parts[2:])

    def _store_synced_draft(self, driver, draft: dict) -> None:
        context = deepcopy(driver.support_context_json or {})
        context["registration_draft"] = deepcopy(draft)
        context["registration_backend_synced_at"] = datetime.utcnow().isoformat()
        driver.support_context_json = context
        flag_modified(driver, "support_context_json")

    def _failure_reply(self, driver, application, exc: Exception) -> StructuredReply:
        driver.state = "yandex_error"
        details = f"Yandex error: {str(exc)}"
        return StructuredReply(
            text=f"{FAILURE_PREFIX}\n\n{details}\n\n{CONFIRM_RETRY_TEXT}",
            next_flow="yandex_error",
            flow_state="yandex_error",
            metadata={
                "intent": "confirmation",
                "draft_ready_for_yandex": True,
                "yandex_auto_submit": True,
                "yandex_submit_status": "error",
                "yandex_error": str(exc),
                "application_status": application.status,
            },
        )

    def _draft(self, driver) -> dict | None:
        context = driver.support_context_json or {}
        draft = context.get("registration_draft")
        return draft if isinstance(draft, dict) else None

    def _missing_yandex_config(self) -> bool:
        return bool(get_settings().missing_config().get("yandex"))

    def _clean(self, value) -> str:
        return repair_mojibake(str(value)).strip() if value is not None else ""
