from sqlalchemy.orm import Session

from app.ai_traces.models import MessageAITrace
from app.messages.models import Message


def upsert_message_ai_trace(
    db: Session,
    *,
    message: Message,
    driver_id: int,
    state_before: str,
    input_text: str | None,
    provider: str,
    intent: str,
    confidence: float,
    next_state: str | None,
    reply_preview: str | None,
    extracted_fields_json: dict | None,
    normalized_fields_json: dict | None,
    reasoning_summary: str | None,
    fallback_used: bool,
    fallback_reason: str | None,
    validation_errors_json: list | dict | None,
    suggested_next_action: str | None,
    raw_decision_json: dict | None,
    final_decision_json: dict | None,
) -> MessageAITrace:
    trace = message.ai_trace or MessageAITrace(message_id=message.id, driver_id=driver_id)
    trace.state_before = state_before
    trace.input_text = input_text
    trace.provider = provider
    trace.intent = intent
    trace.confidence = confidence
    trace.next_state = next_state
    trace.reply_preview = reply_preview
    trace.extracted_fields_json = extracted_fields_json
    trace.normalized_fields_json = normalized_fields_json
    trace.reasoning_summary = reasoning_summary
    trace.fallback_used = fallback_used
    trace.fallback_reason = fallback_reason
    trace.validation_errors_json = validation_errors_json
    trace.suggested_next_action = suggested_next_action
    trace.raw_decision_json = raw_decision_json
    trace.final_decision_json = final_decision_json
    db.add(trace)
    db.flush()
    return trace
