from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.applications.models import Application
from app.applications.service import set_application_status
from app.conversation_events.service import create_conversation_event
from app.database.session import SessionLocal
from app.dialog.engine import DialogueEngine
from app.dialog.prompts import PROMPTS
from app.dialog.states import DialogueState
from app.drivers.models import Driver
from app.messages.service import create_message
from app.whatsapp.sender import WhatsAppSender


REGISTRATION_REMINDER_STATES = {
    DialogueState.ASK_FULL_NAME.value,
    DialogueState.ASK_PHONE.value,
    DialogueState.ASK_CITY.value,
    DialogueState.ASK_ADDRESS.value,
    DialogueState.ASK_IIN.value,
    DialogueState.ASK_BIRTH_DATE.value,
    DialogueState.ASK_DRIVING_EXPERIENCE_SINCE.value,
    DialogueState.ASK_HAS_CAR.value,
    DialogueState.ASK_EXISTING_VEHICLE_IDENTIFIER.value,
    DialogueState.ASK_CAR_BRAND.value,
    DialogueState.ASK_CAR_MODEL.value,
    DialogueState.ASK_CAR_YEAR.value,
    DialogueState.ASK_CAR_PLATE.value,
    DialogueState.ASK_CAR_COLOR.value,
    DialogueState.ASK_CAR_REGISTRATION_CERTIFICATE.value,
    DialogueState.ASK_DRIVER_LICENSE_NUMBER.value,
    DialogueState.ASK_DRIVER_LICENSE_ISSUE_DATE.value,
    DialogueState.ASK_DRIVER_LICENSE_EXPIRES_AT.value,
    DialogueState.ASK_EMPLOYMENT_TYPE.value,
    DialogueState.ASK_HIRED_AT.value,
    DialogueState.ASK_HEARING_IMPAIRED.value,
    DialogueState.ASK_DRIVER_LICENSE_FRONT.value,
    DialogueState.ASK_DRIVER_LICENSE_BACK.value,
    DialogueState.ASK_ID_CARD.value,
    DialogueState.ASK_VEHICLE_REGISTRATION_DOC.value,
}

YANDEX_FOLLOWUP_STATES = {
    DialogueState.ASK_YANDEX_PRO_LOGIN.value,
    DialogueState.ASK_YANDEX_PRO_PROBLEM_DETAILS.value,
}


def _context(driver: Driver) -> dict:
    context = driver.support_context_json or {}
    return context if isinstance(context, dict) else {}


def _set_context(driver: Driver, context: dict | None) -> None:
    driver.support_context_json = context or None
    driver.updated_at = datetime.utcnow()


def _already_reminded_for_step(driver: Driver, state: str) -> bool:
    context = _context(driver)
    return context.get("last_reminder_state") == state


def _record_reminder(driver: Driver, state: str) -> int:
    context = _context(driver)
    count = int(context.get("reminder_count") or 0) + 1
    context["last_reminder_at"] = datetime.utcnow().isoformat()
    context["last_reminder_state"] = state
    context["reminder_count"] = count
    _set_context(driver, context)
    return count


def _send_reminder(db, sender: WhatsAppSender, driver: Driver, application: Application, text: str, event_type: str) -> None:
    result = sender.send_text(driver.whatsapp_phone, text)
    create_message(
        db,
        driver=driver,
        direction="outgoing",
        sender_type="bot",
        message_type="text",
        text=text,
        provider_message_id=str(result),
        delivery_status="sent",
        raw_payload=result,
    )
    create_conversation_event(db, driver, event_type, {"state": driver.state, "text": text})
    db.add(driver)
    db.add(application)


def main() -> int:
    now = datetime.utcnow()
    sender = WhatsAppSender()
    engine = DialogueEngine()
    with SessionLocal() as db:
        rows = db.scalars(
            select(Driver)
            .options(selectinload(Driver.applications))
            .where(Driver.dialog_mode == "bot_active")
        ).all()
        for driver in rows:
            application = driver.applications[0] if driver.applications else None
            if not application or not driver.last_message_at:
                continue
            state = driver.state or DialogueState.NEW.value
            if state in REGISTRATION_REMINDER_STATES and driver.last_message_at <= now - timedelta(hours=4):
                if _already_reminded_for_step(driver, state):
                    continue
                prompt = PROMPTS.get(DialogueState(state), "")
                text = f"Напоминаю про регистрацию.\n\nТекущий шаг:\n{prompt}".strip()
                _send_reminder(db, sender, driver, application, text, "registration_reminder_sent")
                if _record_reminder(driver, state) >= 2:
                    driver.requires_attention = True
                    set_application_status(db, application, "awaiting_manager_review", yandex_status="human_required")
                    create_conversation_event(db, driver, "human_required", {"reason": "registration_reminder_timeout"})
            elif state in YANDEX_FOLLOWUP_STATES and driver.last_message_at <= now - timedelta(hours=2):
                if _already_reminded_for_step(driver, state):
                    continue
                prompt = engine._step_instruction_reply(DialogueState.ASK_YANDEX_PRO_LOGIN)
                text = f"Напоминаю про вход в Яндекс Про.\n\n{prompt}\n\nЕсли не получается, напишите: менеджер."
                _send_reminder(db, sender, driver, application, text, "yandex_followup_reminder_sent")
                if _record_reminder(driver, state) >= 2:
                    driver.requires_attention = True
                    set_application_status(db, application, "awaiting_manager_review", yandex_status="human_required")
                    create_conversation_event(db, driver, "human_required", {"reason": "yandex_followup_timeout"})
        db.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
