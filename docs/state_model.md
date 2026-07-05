# n8n state model for SD Family Taxi

Date: 2026-06-24

This document defines the minimum state fields that should live in PostgreSQL and be used by n8n as the source of truth.

## 1. State contract

The workflow should load and persist state from PostgreSQL on every message.

## 2. Required fields

- `driver_id`
- `phone`
- `state`
- `dialog_mode`
- `support_context`
- `pending_correction_context`
- `last_intent`
- `last_message_at`
- `application_status`
- `requires_attention`

## 3. Field meanings

### driver_id

Primary driver identifier in the database.

### phone

WhatsApp phone number in canonical format.

### state

Current dialog state, such as:

- registration step;
- support step;
- manual mode;
- completed;
- error;
- waiting_documents.

### dialog_mode

High-level mode of operation.

Suggested values:

- `auto`
- `manual`
- `paused`
- `closed`

### support_context

Structured context for support conversations.

Suggested content:

- topic;
- created_at;
- source message;
- problem summary;
- current substep.

### pending_correction_context

Context for field correction requests.

Suggested content:

- target field;
- previous value;
- source state;
- awaiting new value.

### last_intent

The latest AI Router intent that was accepted by n8n.

### last_message_at

Timestamp of the last message from the driver.

### application_status

Business status of the application.

Examples:

- collecting_data
- waiting_documents
- confirming_data
- ready_to_send_yandex
- sending_to_yandex
- sent_to_yandex
- completed
- yandex_error
- duplicate_rejected
- manual_review

### requires_attention

Boolean flag for manual review, escalation, or exceptional handling.

## 4. Recommended JSON substructures

### support_context

```json
{
  "topic": "yandex_problem",
  "subtopic": "park_not_visible",
  "source_message": "Парк көрінбей тұр",
  "started_at": "2026-06-24T10:00:00Z",
  "last_step": "asked_for_screenshot"
}
```

### pending_correction_context

```json
{
  "field": "full_name",
  "previous_value": "Ivan Ivanov",
  "source_state": "confirm_data",
  "awaiting_new_value": true
}
```

## 5. Persistence rules

- Always reload state from PostgreSQL before deciding a branch.
- Always write back state after decision and before outbound reply.
- Never let n8n keep conversation state only in memory.
- Use explicit transitions rather than implicit branching by text alone.

## 6. Manual mode rules

- If `dialog_mode=manual`, the bot must stay silent.
- Manual mode can only be lifted by a manager or by a deliberate admin action.
- Any operator request should set `dialog_mode=manual` immediately.

