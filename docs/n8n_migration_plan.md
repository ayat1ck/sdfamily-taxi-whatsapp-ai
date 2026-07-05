# SD Family Taxi: migration plan to n8n v2

Date: 2026-06-24

## 1. Goal

Move the WhatsApp conversational orchestration into n8n while keeping the current Python microservice as the source of truth for business logic, PostgreSQL, document handling, Yandex Fleet operations, and admin tooling during migration.

Target architecture:

WhatsApp Cloud API
-> n8n Webhook
-> Normalize message
-> Load driver/application state from PostgreSQL
-> AI Router returns strict JSON
-> Switch by intent
-> Registration / Existing Driver / Support / Manager / Yandex Problem / Tariffs / Payouts
-> Save state
-> Send WhatsApp reply
-> Log message

## 2. Migration principles

- PostgreSQL is the source of truth.
- n8n must never rely on memory-only state.
- AI only classifies and suggests. AI never writes to the database directly.
- Any operator/manager request immediately enables manual mode.
- After manual mode is enabled, the bot must stop replying automatically.
- Images/documents must not be rejected with "text expected" style responses.
- If support context exists, an image should be treated as a screenshot/problem attachment.
- If correction context exists, an image should be treated as a new document for the correction.
- The current microservice is not deleted during migration.
- Old and new flows must be runnable in parallel until cutover.

## 3. What moves to n8n

### Phase 1: orchestration

- WhatsApp webhook receiver.
- Message normalization.
- Intent routing.
- Reply selection by branch.
- Manual mode switch.
- Basic state persistence in PostgreSQL.
- Logging of inbound and outbound messages.

### Phase 2: conversational branches

- Registration routing.
- Existing driver support.
- Support escalation.
- Tariff support.
- Payout support.
- Yandex Pro problem handling.
- Blocking and priority support.
- Document and screenshot handling.

### Phase 3: operational utilities

- Retry entry points for managers.
- Retry of failed integrations.
- Reprocessing of messages.
- Observability hooks and status dashboards.

## 4. What stays in the microservice

Keep the Python service as-is initially for:

- PostgreSQL schema and migrations.
- Driver/application/document models.
- Existing Yandex Fleet client and payload mapping.
- Existing document extraction and normalization helpers.
- Admin UI.
- Audit log and conversation events.
- Any complex business rules that are safer to keep deterministic.

## 5. Suggested split of responsibilities

### n8n

- Receive and normalize WhatsApp payloads.
- Load current state from PostgreSQL.
- Ask AI Router for a strict JSON classification.
- Decide the final action.
- Route the message to the correct branch.
- Update state rows.
- Send WhatsApp replies.
- Write logs.

### Python service

- Read/write DB entities when n8n calls it as an API.
- Handle Yandex Fleet submission and retries.
- Store and serve document metadata.
- Serve admin pages.
- Keep all historical business logic available as a reference.

## 6. Recommended data ownership

### PostgreSQL tables that remain authoritative

- drivers
- vehicles
- applications
- documents
- messages
- conversation_events
- integration_jobs
- audit logs

### New or extended state fields

Use a dedicated state record or extend `drivers` / `applications` with:

- state
- dialog_mode
- support_context
- pending_correction_context
- last_intent
- last_message_at
- application_status
- requires_attention

## 7. Migration stages

### Stage A: Discovery and mapping

- Inventory current flows from Python.
- Map intents and states to n8n branches.
- Define PostgreSQL state contract.
- Define AI Router JSON schema.
- Define rollback switch.

### Stage B: Shadow mode

- n8n receives webhook copies.
- n8n classifies messages but does not send replies.
- Compare n8n classification with current microservice behavior.
- Log mismatches.

### Stage C: Partial cutover

- Move low-risk flows first:
  - FAQ
  - tariff questions
  - payout questions
  - simple support
- Keep registration and Yandex submission in Python if needed.

### Stage D: Main cutover

- Move registration routing and state updates into n8n.
- Keep Yandex submission in Python or API-backed service until stable.
- Keep admin UI in Python.

### Stage E: Stabilization

- Reduce Python dependency where n8n is now reliable.
- Add monitoring, dead-letter handling, replay support.
- Document final operating model.

## 8. Risks

### 8.1. State drift

Risk: n8n and Python disagree about current state.

Mitigation:

- one source of truth in PostgreSQL;
- versioned state updates;
- atomic writes;
- logging of every transition.

### 8.2. AI misclassification

Risk: the AI Router returns a wrong intent.

Mitigation:

- strict JSON schema;
- confidence threshold;
- deterministic override rules;
- manual mode for ambiguous or high-risk cases.

### 8.3. Document handling regressions

Risk: screenshots and documents are routed incorrectly.

Mitigation:

- context-aware attachment handling;
- separate support_context and correction_context;
- no hard rejection for image-only replies.

### 8.4. Yandex Fleet integration failures

Risk: submission flow breaks when moved.

Mitigation:

- keep current Python client as API-backed fallback;
- retry jobs;
- log request/response payloads;
- preserve rollback path.

### 8.5. Manual mode mistakes

Risk: bot keeps answering after escalation.

Mitigation:

- hard manual flag in PostgreSQL;
- early exit in every workflow branch;
- no auto-send when manual mode is active.

## 9. Rollback plan

Rollback must be simple and fast.

### Rollback trigger examples

- incorrect intent routing at scale;
- wrong state updates;
- broken document handling;
- Yandex submission errors;
- operator complaints about duplicate replies.

### Rollback actions

1. Disable n8n reply sending.
2. Route webhook traffic back to the Python microservice.
3. Keep n8n in shadow mode only.
4. Reconcile recent messages and state changes.
5. Fix the workflow and re-run shadow comparison.

### Rollback rule

The Python microservice must stay deployable until n8n is proven stable in production.

## 10. Success criteria

- All WhatsApp messages are received by n8n.
- Intent classification is stable and reproducible.
- PostgreSQL remains the source of truth.
- Manual mode always stops bot replies.
- Registration, support, and escalation branches behave deterministically.
- Documents are not blocked by simplistic text-only validation.
- The microservice can still be used as a fallback API.

