# n8n workflow blueprint for SD Family Taxi

Date: 2026-06-24

This file describes the target n8n v2 workflow set and how each flow should behave.

## 1. Global design

Shared workflow pattern:

1. Receive WhatsApp webhook.
2. Normalize payload into a canonical message object.
3. Load driver/application state from PostgreSQL.
4. Enforce manual mode and priority rules.
5. Call AI Router for strict JSON classification when needed.
6. Route by intent.
7. Persist state and logs.
8. Send reply or remain silent.

## 2. Workflow: WhatsApp Incoming Router

### Purpose

Entry point for every inbound WhatsApp message.

### Responsibilities

- validate webhook payload;
- extract phone, message type, text, media metadata, message id;
- load driver and application row from PostgreSQL;
- check manual mode;
- check blocking priority conditions;
- choose downstream workflow.

### Routing rules

- If `dialog_mode=manual`, do not reply.
- If sender requested operator/manager, set manual mode and stop auto replies.
- If attachment exists, pass it to Document/Screenshot Handler.
- Otherwise call AI Router.

## 3. Workflow: Registration Flow

### Purpose

Handle new driver registration and corrections during onboarding.

### Inputs

- `registration` intent;
- `driver_update_request` intent when user edits registration fields;
- attachment with registration context;
- manual form text.

### Main steps

1. Load registration state from PostgreSQL.
2. Merge extracted fields into a structured registration draft.
3. Validate fields deterministically.
4. Ask follow-up question if a required field is missing.
5. Build summary when enough fields exist.
6. Wait for confirmation.
7. Save state transitions.
8. Send reply.

### Notes

- AI may suggest fields, but the workflow decides whether to accept them.
- If the user sends a photo during registration, treat it as a document, not as a text failure.

## 4. Workflow: Existing Driver Support

### Purpose

Support drivers who are already connected or already registered.

### Typical cases

- "I am already connected"
- "I already have a profile"
- change car or personal data
- ask for help after registration

### Behavior

- detect existing driver flags;
- avoid starting a new registration;
- route to a support branch or change-request branch;
- keep the current state intact unless explicitly reset.

## 5. Workflow: Payout Support

### Purpose

Answer money withdrawal and payout-related questions.

### Typical user questions

- "How do I withdraw money?"
- "When will money arrive?"
- "Why didn't payout happen?"

### Behavior

- use FAQ/support reply templates;
- if the question is operational and sensitive, escalate to manager;
- never change registration state.

## 6. Workflow: Tariff Support

### Purpose

Handle tariff and service-class questions.

### Typical user questions

- "Enable Comfort"
- "Enable Intercity"
- "Disable tariff"
- "Why no orders?"

### Behavior

- classify as tariff_support or faq;
- if the answer requires manual park action, set requires_attention and manual mode;
- send a concise operational reply.

## 7. Workflow: Yandex Pro Problem

### Purpose

Resolve Yandex Pro login or visibility problems.

### Typical user questions

- "No park in Yandex Pro"
- "Cannot log in"
- "SMS not coming"
- "Account inactive"
- "Park not visible"

### Behavior

- load support context if already active;
- present step-by-step troubleshooting;
- if user provides a screenshot, treat it as issue evidence;
- if the issue is unresolved, escalate to manager.

## 8. Workflow: Blocking/Priority Support

### Purpose

Intercept high-priority requests before normal flow.

### Typical triggers

- operator / manager request;
- blocking access;
- account inactive;
- "park not visible";
- urgent support;
- duplicate complaint;
- direct human help request.

### Behavior

- set manual mode;
- mark requires_attention;
- persist reason;
- stop auto replies after the handoff message.

## 9. Workflow: Manager Escalation

### Purpose

Create a durable handoff to a human manager.

### Responsibilities

- set manual mode in PostgreSQL;
- log escalation reason;
- notify manager channel if configured;
- send one final user-facing handoff message;
- ensure future messages do not trigger auto-replies.

### Example final reply

"Ваш запрос передан менеджеру. Ожидайте ответа."

## 10. Workflow: Document/Screenshot Handler

### Purpose

Handle attachments with context-aware routing.

### Rules

- Do not reply with "text expected" when the user sends an image.
- If support_context exists, treat the image as a screenshot/problem attachment.
- If pending_correction_context exists, treat the image as a new document for correction.
- If registration is active, treat the image as a registration document.

### Outputs

- store file metadata in PostgreSQL;
- optionally upload to Google Drive through the existing API or Python service;
- mark document status;
- move to the next workflow step.

## 11. Intent mapping

### Required intents

- registration
- existing_driver_support
- human_operator
- payout_support
- tariff_support
- yandex_problem
- application_status
- driver_update_request
- smz_request
- blocking_priority_support
- rental_car_question
- courier_registration
- faq
- unknown

### Suggested sub-branches

- `faq` can further carry labels like `cities`, `bonus`, `general`.
- `unknown` should fall back to a safe clarification or manager route.

## 12. Branch output contract

Every workflow branch should write:

- incoming log;
- AI result;
- final routing decision;
- state update;
- outbound log or manual-mode flag.

## 13. Required stopping rules

- If manual mode is set, stop replying automatically.
- If the intent is ambiguous and confidence is low, do not guess aggressively.
- If a user asks for a manager, do not continue the registration flow.
- If a support context exists and an attachment arrives, never reject it for being non-text.

