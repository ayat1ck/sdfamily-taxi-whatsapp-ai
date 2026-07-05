# SD Family Taxi branching rules for n8n

Date: 2026-06-24

This document explains how intents, state, and attachments should be routed.

## 1. Intent routing

### registration

Route to:

- Registration Flow

### existing_driver_support

Route to:

- Existing Driver Support

### human_operator

Route to:

- Manager Escalation

### payout_support

Route to:

- Payout Support

### tariff_support

Route to:

- Tariff Support

### yandex_problem

Route to:

- Yandex Pro Problem

### application_status

Route to:

- Existing Driver Support or Status-specific support branch

### driver_update_request

Route to:

- Registration Flow
- or correction sub-branch if registration is already in progress

### smz_request

Route to:

- Registration Flow
- or human review if the request needs park action

### blocking_priority_support

Route to:

- Blocking/Priority Support
- then Manager Escalation if it is not resolved automatically

### rental_car_question

Route to:

- FAQ or dedicated rental info response

### courier_registration

Route to:

- Registration Flow with courier-specific path

### faq

Route to:

- FAQ branch

### unknown

Route to:

- Safe clarification
- or Manager Escalation if context indicates risk

## 2. Manual mode rules

- Any request containing `оператор`, `менеджер`, `живой человек`, `support`, or equivalent should set `dialog_mode=manual`.
- After manual mode is set, the bot must stay silent.
- Manual mode is a PostgreSQL flag, not an n8n memory flag.

## 3. Attachment rules

### Image or document during support context

Treat as screenshot or evidence for the current support topic.

### Image or document during correction context

Treat as a new document for the target field correction.

### Image or document during registration

Treat as a registration document.

### No support or registration context

If there is no clear context, preserve the file, log it, and route to clarification or human review.

## 4. Support context influence

If `support_context` exists, it should bias the next message interpretation toward the same support topic.

Examples:

- previous topic was `yandex_problem` and user sends an image -> screenshot of Yandex issue;
- previous topic was `payout_support` and user sends a screenshot -> payout evidence;
- previous topic was `blocking_priority_support` and user sends a screenshot -> blocking evidence.

## 5. Correction context influence

If `pending_correction_context` exists, the next non-empty value or attachment should be routed as a correction, not as a fresh registration.

Examples:

- target field is `full_name` -> text or document may update name data;
- target field is `vehicle_document` -> document should be stored as replacement evidence.

## 6. Confidence thresholds

- `confidence >= 0.65`: normal branch processing.
- `confidence < 0.65`: manager escalation unless the state is clearly deterministic.

## 7. Error handling

Escalate to manager when:

- AI returns invalid JSON;
- AI returns low confidence;
- payload is ambiguous and high-risk;
- the user explicitly asks for a human;
- a state update fails;
- a WhatsApp send fails and should not be retried silently.

