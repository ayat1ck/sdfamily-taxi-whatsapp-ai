# Project Context for Rewrite

Date: 2026-07-05

This document is the single-file context pack for rewriting the SD Family Taxi WhatsApp bot.
It combines the project goal, current architecture, key runtime files, state model, integrations, and practical constraints.

## 1. Project Goal

The project is a backend service for a taxi park WhatsApp bot.

Its job is to:

- receive inbound WhatsApp messages;
- understand whether the user is a new driver, an existing driver, or a support case;
- guide new drivers through registration;
- collect profile data and documents;
- extract fields from uploaded images and PDFs when possible;
- submit completed applications to Yandex Fleet / Yandex Pro;
- help existing drivers with support topics;
- show managers an admin view of chats, applications, documents, and traces.

This is not a pure chat bot.
It is a backend-first workflow engine with AI assistance.

## 2. High-Level Architecture

Current architecture:

- FastAPI handles the HTTP layer.
- WhatsApp webhook receives messages.
- `app/dialog/engine.py` is the main orchestration layer.
- `app/dialog/ai.py` is the AI and intent extraction layer.
- `app/documents` handles document intake and OCR-like extraction.
- `app/integrations/yandex` handles submission and Yandex-side mapping.
- `app/admin` provides the manager/admin interface.
- SQLAlchemy models store state, messages, applications, audit logs, and traces.

Core principle:

- backend owns control flow;
- state machine and hardcoded routing outrank model guesses;
- AI helps classify, extract, and answer FAQs;
- critical business logic stays in code.

## 3. Repository Structure

Main areas in the repository:

- `app/whatsapp` - webhook, parsing, media, sender;
- `app/dialog` - dialogue state machine, AI routing, prompts, FAQ;
- `app/documents` - document processing, extraction, registration flow;
- `app/integrations/yandex` - Yandex client, mapper, schemas, submission service;
- `app/integrations/google_*` - Google Drive / Sheets support;
- `app/drivers` - driver domain model and services;
- `app/vehicles` - vehicle domain model and services;
- `app/applications` - application lifecycle and status handling;
- `app/messages` - message persistence;
- `app/conversation_events` - business journey events;
- `app/ai_traces` - AI decision traces;
- `app/audit` - audit trail;
- `app/admin` - admin console;
- `app/debug` - debug endpoints;
- `docs` - design notes, migration notes, testing guides;
- `tests` - regression and flow tests;
- `n8n/workflows` - current or planned workflow automation files.

## 4. Runtime Entry Points

Most important runtime files:

- [`app/main.py`](../app/main.py)
- [`app/whatsapp/webhook.py`](../app/whatsapp/webhook.py)
- [`app/dialog/engine.py`](../app/dialog/engine.py)
- [`app/dialog/ai.py`](../app/dialog/ai.py)
- [`app/dialog/states.py`](../app/dialog/states.py)
- [`app/documents/registration_flow.py`](../app/documents/registration_flow.py)
- [`app/documents/extraction.py`](../app/documents/extraction.py)
- [`app/integrations/yandex/service.py`](../app/integrations/yandex/service.py)

Flow summary:

1. WhatsApp sends an inbound payload.
2. Webhook parses and normalizes the payload.
3. The engine loads or creates driver/application state.
4. The engine stores the incoming message.
5. Routing decides whether the message is registration, support, document upload, special command, or escalation.
6. AI is called only when needed.
7. Replies are stored and sent back through WhatsApp.
8. When data is complete, the backend submits to Yandex.

## 5. Current Business Goal

The bot should:

- convert a first WhatsApp message into a controlled onboarding flow;
- keep the user in the right state;
- collect all required registration data;
- accept documents in a structured way;
- avoid asking the same thing repeatedly;
- hand off hard support cases to a human;
- help the driver finish Yandex Pro onboarding after submission;
- preserve observability for managers and debugging.

## 6. Main Runtime Rule

The order of routing in `DialogueEngine.handle_message(...)` matters more than the raw message content.

In practice the engine checks, in broad terms:

- pending menu or active support context;
- existing driver / operator / escalation interrupts;
- duplicate and special states;
- media and document handling;
- active registration states;
- AI-assisted parsing and clarification;
- FAQ or support help fallback;
- Yandex submission or post-submission follow-up.

If a message matches an earlier branch, later branches do not run.

## 7. Dialogue States

The canonical states live in [`app/dialog/states.py`](../app/dialog/states.py).

Current state groups:

- `NEW`
- registration text steps;
- registration document steps;
- confirmation and submission steps;
- Yandex follow-up steps;
- support/error/duplicate branches;
- `COMPLETED`

State values currently include:

- `ask_full_name`
- `ask_executor_type`
- `ask_phone`
- `ask_city`
- `ask_address`
- `ask_iin`
- `ask_birth_date`
- `ask_driving_experience_since`
- `ask_has_car`
- `ask_existing_vehicle_identifier`
- `ask_car_brand`
- `ask_car_model`
- `ask_car_year`
- `ask_car_plate`
- `ask_car_color`
- `ask_car_registration_certificate`
- `ask_driver_license_number`
- `ask_driver_license_issue_date`
- `ask_driver_license_expires_at`
- `ask_employment_type`
- `ask_hired_at`
- `ask_hearing_impaired`
- `ask_driver_license_front`
- `ask_driver_license_back`
- `ask_id_card`
- `ask_vehicle_registration_doc`
- `ask_selfie_with_license`
- `ask_rent_or_power_of_attorney`
- `confirm_data`
- `ready_to_send_yandex`
- `sending_to_yandex`
- `sent_to_yandex`
- `ask_yandex_pro_login`
- `ask_yandex_pro_problem_details`
- `yandex_error`
- `duplicate_rejected`
- `completed`

## 8. Registration Flow

The registration flow is split between:

- `app/documents/registration_flow.py`
- `app/dialog/engine.py`

What it does:

- asks for personal data step by step;
- asks for vehicle data step by step;
- accepts documents;
- normalizes obvious user input;
- moves to confirmation when all required data is present;
- submits to Yandex after confirmation.

Important design idea:

- text fields and documents are not treated as the same thing;
- the backend can use manual mode or skip certain document steps when needed;
- OCR-like extraction is a helper, not the final source of truth.

## 9. Document Handling

Document intake lives in `app/documents`.

Main responsibilities:

- accept images and PDFs from WhatsApp;
- recognize document type;
- detect when one file contains multiple useful sides or documents;
- extract fields from the document;
- normalize the extracted data;
- store the document metadata;
- connect the document to the current driver/application.

Supported document context includes:

- driver license front;
- driver license back;
- ID card;
- vehicle registration certificate;
- selfie or unsupported uploads in certain flows.

Important:

- document extraction is optional and feature-flagged;
- extracted values are normalized before being applied;
- if extraction fails, the backend should still keep the flow understandable.

## 10. AI Layer

The AI layer is implemented in [`app/dialog/ai.py`](../app/dialog/ai.py).

Current design:

- deterministic AI runs first;
- LLM is optional;
- LLM is mostly used for FAQ assistance or a full completion-state mode;
- backend can override or ignore model output when it conflicts with flow rules.

Main AI responsibilities:

- detect intent;
- classify support vs registration;
- extract field values from noisy text;
- help with clarifications;
- optionally answer KB-based questions.

Important rule:

- AI is not the source of truth for state transitions;
- backend validation still decides whether something is accepted.

## 11. Prompt Layer

Prompt logic is built in `app/dialog/llm_prompt.py`.

The prompts are meant to:

- constrain the model to structured output;
- keep the model inside allowed next states;
- prevent hallucination;
- enforce normalization rules for dates, phones, documents, and vehicle fields;
- distinguish support, FAQ, registration, confirmation, and corrections.

## 12. Support and Existing Driver Flows

The bot handles existing drivers separately from new registration.

This matters because an already connected driver should not be dropped back into onboarding.

Support flows include topics such as:

- Yandex Pro login;
- SMS / verification code;
- inactive account;
- going online;
- tariffs;
- payout questions;
- profile or document changes;
- manager escalation.

Support can be:

- a one-shot FAQ answer;
- a guided step-by-step flow;
- a handoff to a human manager.

## 13. Yandex Integration

Yandex logic lives in `app/integrations/yandex`.

Responsibilities:

- build a submission payload;
- map local fields to Yandex fields;
- validate the payload before sending;
- submit the application or profile update;
- store Yandex IDs and submission status;
- handle partial submission errors and recovery paths;
- sync or find existing profiles when needed.

Important:

- submission should not depend on the LLM;
- the backend validates required fields before the external call;
- if Yandex rejects something, the bot should preserve the error and guide the user.

## 14. Data Model

Core business entities:

- `Driver`
- `Vehicle`
- `Application`
- `Message`
- `ConversationEvent`
- `IntegrationJob`
- `ApplicationAuditLog`

What each one does:

- `Driver` stores the person, current flow state, support mode, and registration data;
- `Vehicle` stores car data;
- `Application` stores the lifecycle and submission status;
- `Message` stores inbound and outbound chat messages;
- `ConversationEvent` stores important journey milestones;
- `IntegrationJob` tracks external/integration work;
- `ApplicationAuditLog` stores administrative audit history.

## 15. Observability

The system is designed to be inspectable.

Observed layers:

- message persistence;
- AI traces;
- conversation events;
- audit logs;
- application status;
- integration job records;
- admin console views.

This is important because many failures are not obvious from the final reply alone.

## 16. What Works Today

Current system strengths:

- registration flow exists and is stateful;
- documents are accepted and processed;
- support topics are separated from onboarding in many cases;
- Yandex submission is explicit and validated;
- admin views and traces exist;
- tests already cover multiple regressions.

## 17. Where the System Is Fragile

The weakest parts are usually:

- branch priority mistakes;
- ambiguous user wording;
- support and registration collisions;
- messy free-form text;
- imperfect document-to-state alignment;
- Yandex/local profile mismatches;
- old fallback phrasing that can be inconsistent.

This means most fixes should be:

- routing fixes;
- state-machine fixes;
- better normalization;
- better observability;
- not only prompt changes.

## 18. Current Practical Reading Order

If someone needs to understand the bot quickly, read in this order:

1. [`app/dialog/engine.py`](../app/dialog/engine.py)
2. [`app/documents/registration_flow.py`](../app/documents/registration_flow.py)
3. [`app/dialog/faq.py`](../app/dialog/faq.py)
4. [`app/dialog/ai.py`](../app/dialog/ai.py)
5. [`app/dialog/llm_prompt.py`](../app/dialog/llm_prompt.py)
6. [`app/documents/extraction.py`](../app/documents/extraction.py)
7. [`app/integrations/yandex/service.py`](../app/integrations/yandex/service.py)

## 19. Existing Docs Worth Keeping

Relevant docs already in the repository:

- `docs/architecture_overview.md`
- `docs/project_description_and_goal.md`
- `docs/bot_backend_ai_full_reference.md`
- `docs/bot_logic_tz_for_rewrite.md`
- `docs/state_model.md`
- `docs/data_contract.md`
- `docs/postgres_schema.md`
- `docs/n8n_migration_plan.md`
- `docs/n8n_workflows.md`
- `docs/test_scenarios.md`
- `docs/test_run_guide.md`
- `docs/test_payloads.md`

These docs already contain a lot of the raw material for a rewrite.

## 20. Suggested Rewrite Strategy

If the bot is being rewritten, the safest approach is:

- preserve the current domain rules first;
- keep the state machine explicit;
- isolate support flows from registration;
- keep document intake separate from text parsing;
- preserve Yandex validation in code;
- reduce dependence on prompt-only behavior;
- add regression tests around known tricky phrases and state transitions.

## 21. Short Summary

This project is:

- a WhatsApp onboarding and support backend for a taxi park;
- stateful and workflow-driven;
- AI-assisted, not AI-owned;
- integrated with documents, Google services, and Yandex;
- heavily dependent on deterministic routing and persisted state.

The key rewrite rule is simple:

- do not replace the backend logic with a prompt;
- make the state machine cleaner, more explicit, and easier to debug.
