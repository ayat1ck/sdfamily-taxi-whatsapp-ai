from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import datetime, timedelta

from sqlalchemy.orm.attributes import flag_modified

from app.dialog_v2.response import StructuredReply
from app.dialog_v2.ui import MANAGER_TRIAGE_BUTTONS, buttons_reply

# After this many consecutive misunderstandings the dialog goes to a manager.
MAX_MISSES = 3
# Third identical bot reply in a row escalates to a manager.
MAX_REPEATS = 2
SESSION_TTL = timedelta(hours=6)

UNSUPPORTED_MEDIA_TEXTS = {
    "audio": (
        "Я пока не умею слушать голосовые сообщения.\n"
        "Напишите, пожалуйста, текстом — или выберите, что нужно:"
    ),
    "video": (
        "Видео я обработать не могу.\n"
        "Если это документ — отправьте его фото или PDF. Или выберите, что нужно:"
    ),
    "sticker": "Стикеры я не распознаю.\nНапишите текстом или выберите, что нужно:",
    "unsupported": (
        "Такой тип сообщения я не поддерживаю.\n"
        "Напишите текстом или выберите, что нужно:"
    ),
}

CLARIFY_FIRST = (
    "Я не совсем понял ваше сообщение.\n"
    "Выберите, что нужно, или напишите своими словами:"
)

CLARIFY_SECOND = (
    "Извините, я снова не понял.\n"
    "Нажмите одну из кнопок ниже — или напишите «менеджер», и вам ответит человек:"
)

REPEAT_HINT = "\n\nЕсли я отвечаю не то, напишите «менеджер» — подключится человек."


def _save_context(driver, context: dict) -> None:
    driver.support_context_json = context
    try:
        flag_modified(driver, "support_context_json")
    except Exception:
        # Non-ORM driver objects (tests) don't support flag_modified.
        pass


def _fingerprint(text: str | None) -> str:
    return hashlib.sha1((text or "").strip().encode("utf-8")).hexdigest()


class FallbackPolicy:
    """Central safety net: counts misunderstandings, prevents reply loops,
    expires stale menus and escalates stuck dialogs to a manager."""

    def touch_session(self, driver) -> None:
        context = deepcopy(driver.support_context_json or {})
        now = datetime.utcnow()
        expired = False
        last_seen_raw = context.get("last_seen_at")
        if last_seen_raw:
            try:
                expired = now - datetime.fromisoformat(str(last_seen_raw)) > SESSION_TTL
            except ValueError:
                expired = True
        if expired:
            context.pop("pending_menu", None)
            context.pop("manager_triage_reason", None)
            context.pop("fallback", None)
        context["last_seen_at"] = now.isoformat()
        _save_context(driver, context)

    def reset_misses(self, driver) -> None:
        context = deepcopy(driver.support_context_json or {})
        fallback = dict(context.get("fallback") or {})
        if fallback.get("misses"):
            fallback["misses"] = 0
            context["fallback"] = fallback
            _save_context(driver, context)

    def _bump_misses(self, driver) -> int:
        context = deepcopy(driver.support_context_json or {})
        fallback = dict(context.get("fallback") or {})
        misses = int(fallback.get("misses") or 0) + 1
        fallback["misses"] = misses
        context["fallback"] = fallback
        _save_context(driver, context)
        return misses

    def _set_fallback_menu(self, driver) -> None:
        context = deepcopy(driver.support_context_json or {})
        context["pending_menu"] = "fallback_menu"
        _save_context(driver, context)

    def handle_miss(self, db, driver, application, message, *, kind: str = "unclear_text") -> StructuredReply:
        """Called when no flow understood the message. Escalation ladder:
        miss 1 -> clarify + menu, miss 2 -> firm menu, miss 3 -> manager."""
        misses = self._bump_misses(driver)

        if misses >= MAX_MISSES:
            return self._escalate(db, driver, application, message, reason="bot_did_not_understand")

        text = UNSUPPORTED_MEDIA_TEXTS.get(kind)
        if text is None:
            text = CLARIFY_FIRST if misses == 1 else CLARIFY_SECOND
        self._set_fallback_menu(driver)
        return buttons_reply(
            text,
            MANAGER_TRIAGE_BUTTONS,
            flow="fallback",
            state=driver.state,
            metadata={"intent": "fallback", "fallback_kind": kind, "miss_count": misses},
        )

    def guard_repeat(self, db, driver, application, message, reply: StructuredReply) -> StructuredReply:
        """If the bot is about to send the same reply for the third time in a row,
        hand the dialog to a manager instead of looping."""
        if reply is None or not (reply.text or "").strip():
            return reply
        intent = (reply.metadata or {}).get("intent")
        if reply.requires_manager or intent in {"manager", "manager_triage", "fallback"}:
            return reply

        context = deepcopy(driver.support_context_json or {})
        fallback = dict(context.get("fallback") or {})
        fingerprint = _fingerprint(reply.text)
        if fallback.get("last_reply_hash") == fingerprint:
            repeats = int(fallback.get("repeats") or 0) + 1
        else:
            repeats = 0
        fallback["last_reply_hash"] = fingerprint
        fallback["repeats"] = repeats
        context["fallback"] = fallback
        _save_context(driver, context)

        if repeats >= MAX_REPEATS:
            return self._escalate(db, driver, application, message, reason="bot_loop")
        if repeats == 1:
            reply.text = f"{reply.text}{REPEAT_HINT}"
        return reply

    def _escalate(self, db, driver, application, message, *, reason: str) -> StructuredReply:
        from app.dialog_v2.flows.manager import ManagerHandoffFlow

        context = deepcopy(driver.support_context_json or {})
        context.pop("pending_menu", None)
        context["fallback"] = {"misses": 0, "repeats": 0, "last_reply_hash": None}
        _save_context(driver, context)

        reply = ManagerHandoffFlow().handle(db, driver, application, message, reason=reason, skip_triage=True)
        reply.text = (
            "Похоже, я не могу разобраться с вашим вопросом — передаю менеджеру.\n\n" + reply.text
        )
        reply.metadata["escalation_reason"] = reason
        return reply
