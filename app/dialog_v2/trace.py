from __future__ import annotations

from time import perf_counter

from app.dialog_v2.response import StructuredReply


def build_v2_trace(
    *,
    phone: str,
    message_type: str,
    text: str | None,
    flow: str | None,
    intent: str | None,
    state_before: str | None,
    state_after: str | None,
    pending_menu_before: str | None,
    pending_menu_after: str | None,
    pending_action_before: str | None,
    pending_action_after: str | None,
    reply: StructuredReply | None,
    duration_ms: int,
    error: str | None = None,
) -> dict[str, object]:
    metadata = reply.metadata if reply else {}
    return {
        "phone": phone.lstrip("+"),
        "message_type": message_type,
        "text": text,
        "flow": flow,
        "intent": intent,
        "state_before": state_before,
        "state_after": state_after,
        "pending_menu_before": pending_menu_before,
        "pending_menu_after": pending_menu_after,
        "pending_action_before": pending_action_before,
        "pending_action_after": pending_action_after,
        "global_intent": metadata.get("global_intent"),
        "global_action": metadata.get("global_action"),
        "draft_ready_for_yandex": metadata.get("draft_ready_for_yandex"),
        "missing_fields": metadata.get("missing_fields"),
        "reply_type": reply.type if reply else None,
        "requires_manager": reply.requires_manager if reply else False,
        "manager_reason": (reply.manager_alert or {}).get("reason") if reply and reply.manager_alert else None,
        "events": [event.get("type") for event in (reply.events if reply else []) if isinstance(event, dict)],
        "duration_ms": duration_ms,
        "error": error,
    }


def trace_duration_ms(start: float) -> int:
    return int((perf_counter() - start) * 1000)
