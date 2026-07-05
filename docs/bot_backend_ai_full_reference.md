# SD Family Taxi WhatsApp Bot: Full Backend + AI Reference

## Purpose

This document is a full technical map of how the bot works today:

- how an incoming WhatsApp message is processed
- how the backend decides between registration, FAQ, support, document OCR, and manager escalation
- where AI is actually used and where deterministic code has priority
- what prompts exist
- what priorities and overrides exist
- how document extraction works
- how Yandex submission works
- what debug traces and conversation events are written
- what the main weak spots are

This file is based on the current code in:

- [app/dialog/engine.py](C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/app/dialog/engine.py)
- [app/dialog/ai.py](C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/app/dialog/ai.py)
- [app/dialog/llm_prompt.py](C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/app/dialog/llm_prompt.py)
- [app/dialog/faq.py](C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/app/dialog/faq.py)
- [app/documents/registration_flow.py](C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/app/documents/registration_flow.py)
- [app/documents/extraction.py](C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/app/documents/extraction.py)
- [app/integrations/yandex/service.py](C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/app/integrations/yandex/service.py)

## 1. High-Level Architecture

The bot is not a pure LLM bot.

It is a backend-first state machine with AI assistance.

The real architecture is:

1. WhatsApp webhook receives a message.
2. Backend loads driver, application, state, memory, and saved context.
3. `DialogueEngine.handle_message(...)` becomes the main orchestrator.
4. The engine tries deterministic branches first:
   - pending menus
   - pending support/profile actions
   - operator/manual interruptions
   - unsupported media handling
   - support menu state
   - special commands
   - priority interrupts
   - document handling
   - active registration flow handling
5. `AIService.respond(...)` is called when the engine needs intent extraction, field extraction, FAQ/help classification, or clarification.
6. AI itself is also backend-first:
   - deterministic provider runs first
   - LLM is optional and mostly acts as FAQ assistant or completed-state assistant
7. If registration data becomes complete, backend submits to Yandex through `YandexSubmissionService`.

So the truth is:

- backend owns control flow
- AI helps classify and extract
- LLM is not the main brain
- state machine and hardcoded rules outrank model guesses

## 2. Main Runtime Entry Point

Main runtime entry:

- [app/dialog/engine.py](C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/app/dialog/engine.py): `DialogueEngine.handle_message`

What happens there:

1. Creates or loads `Application` for the driver.
2. Updates `driver.last_message_at` and unread counter.
3. Stores incoming message in DB through `create_message(...)`.
4. Loads memory and remembers message context.
5. Reads current dialogue state from `driver.state`.
6. Runs several routing layers in order.

Important takeaway:

The order of checks in `handle_message` is everything. Most real behavior comes from routing priority, not from model intelligence.

## 3. Core Routing Order

The effective routing order in `handle_message` is roughly:

1. Pending menu handling
2. Pending active action handling
3. Immediate operator interrupt
4. Duplicate-rejected special case
5. Unsupported message handling
6. Stateful support menu handling
7. Special command handling
8. Priority interrupt handling
9. Media/document branch
10. Text branch for `NEW`
11. Text branch for active registration states
12. Post-confirmation and Yandex follow-up logic

This matters because if a message matches an earlier branch, later branches never run.

That is exactly why many user-facing bugs looked random: often the wrong branch won too early.

## 4. Main Data Objects

### Driver

The `Driver` entity is the main runtime memory:

- registration fields
- current state
- support mode and support topic
- fallback count
- unread count
- vehicle relation
- uploaded documents

Key runtime fields used by dialog:

- `state`
- `dialog_mode`
- `requires_attention`
- `active_support_topic`
- `active_support_step`
- `support_context_json`
- `full_name`, `phone`, `city`, `address`, `iin`, dates, etc.

### Application

`Application` tracks the registration/submission lifecycle:

- internal status
- Yandex status
- Yandex IDs
- errors
- sent timestamp

### Message

Every incoming and outgoing message is persisted.

### AI Trace

AI/system decisions are persisted through `upsert_message_ai_trace(...)`.

### Conversation Events

Business milestones are persisted through `create_conversation_event(...)`.

Examples:

- `started_onboarding`
- `support_flow_started`
- `support_escalated_to_manager`
- `existing_driver_support_menu_opened`
- `yandex_pro_login_confirmed`
- `registration_debug_trace`

## 5. State Model

State values live in [app/dialog/states.py](C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/app/dialog/states.py).

In practice states are split into these groups:

### A. New / entry

- `NEW`

### B. Registration document collection

Document states are controlled by [app/documents/registration_flow.py](C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/app/documents/registration_flow.py).

Current document sequence:

1. `ASK_DRIVER_LICENSE_FRONT`
2. `ASK_DRIVER_LICENSE_BACK`
3. `ASK_ID_CARD`
4. `ASK_VEHICLE_REGISTRATION_DOC`

Important:

- selfie step was removed from the required registration sequence
- registration no longer waits for selfie before confirmation

### C. Registration text fields

Text sequence:

1. `ASK_FULL_NAME`
2. `ASK_PHONE`
3. `ASK_CITY`
4. `ASK_ADDRESS`
5. `ASK_IIN`
6. `ASK_BIRTH_DATE`
7. `ASK_DRIVING_EXPERIENCE_SINCE`
8. `ASK_CAR_BRAND`
9. `ASK_CAR_MODEL`
10. `ASK_CAR_YEAR`
11. `ASK_CAR_PLATE`
12. `ASK_CAR_COLOR`
13. `ASK_CAR_REGISTRATION_CERTIFICATE`
14. `ASK_DRIVER_LICENSE_NUMBER`
15. `ASK_DRIVER_LICENSE_ISSUE_DATE`
16. `ASK_DRIVER_LICENSE_EXPIRES_AT`
17. `ASK_EMPLOYMENT_TYPE`
18. `ASK_HIRED_AT`
19. `ASK_HEARING_IMPAIRED`
20. `CONFIRM_DATA`

### D. Submission / post-submit

- `READY_TO_SEND_YANDEX`
- `SENT_TO_YANDEX`
- `ASK_YANDEX_PRO_LOGIN`
- `ASK_YANDEX_PRO_PROBLEM_DETAILS`
- `YANDEX_ERROR`
- `COMPLETED`

### E. Support and duplicate branches

- `DUPLICATE_REJECTED`
- support is mostly handled through `support_context_json` and `active_support_topic`, not through many dedicated states

## 6. Registration Flow Engine

The registration flow is built from two layers:

1. `registration_flow.py` decides what step is still missing
2. `engine.py` decides how to respond to the user and how to apply fields

### How next registration state is computed

`next_registration_state(driver, vehicle)`:

1. Checks whether manual data entry mode is enabled.
2. If not manual mode, checks required document slots first.
3. Then checks text fields in fixed order.
4. Returns `CONFIRM_DATA` when everything is complete.

### Manual data entry mode

There is a manual mode concept:

- `prefers_manual_data_entry(driver)`
- `set_manual_data_entry(driver, enabled=True)`
- `skip_data_documents_for_manual_entry(...)`

This means the backend can stop waiting for documents and continue through text fields.

## 7. Greeting / Entry Behavior

Greeting behavior now exists in two places:

- [app/dialog/faq.py](C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/app/dialog/faq.py): `SMALLTALK_REPLY`
- [app/dialog/ai.py](C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/app/dialog/ai.py): `CASUAL_SMALLTALK_REPLY`

Current greeting CTA:

- says it can help with registration
- offers:
  - `1. Регистрация`
  - `2. Узнать условия`
  - `3. Помощь со входом в Яндекс Про`

Additional hardcoded handling in `NEW` state:

- `1` starts registration
- `2`, `условия`, `условие`, `тарифы`, `комиссия` return park conditions
- `3`, `вход`, `вход в яндекс про`, `яндекс про`, `помощь со входом`, `логин` return login help menu

This menu is not just copy now; it has real routing in the engine.

## 8. How Registration Start Is Detected

Registration start is detected by `_looks_like_registration_start_request(...)` in [app/dialog/engine.py](C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/app/dialog/engine.py).

Important current triggers:

- `1`
- `регистрация`
- `тіркелу`
- `тіркеу`
- `тыркелу`
- `тыркеу`
- words containing registration / connection intent

Also there is typo-specific handling for variants containing:

- `тыркел`
- `тркел`
- `тыркеу`
- `тркеу`

This was added because real users write broken variants constantly.

Important exclusion:

If the message says the person is already connected or already registered, the function should not start onboarding.

## 9. Existing Driver Support

There is a dedicated branch for users who are already in the park.

Detection lives in several places:

- `classify_dialog_intent(...)` in `faq.py`
- `_looks_like_existing_driver_support_intent(...)` in `ai.py`
- priority support handling in `engine.py`

This branch catches messages like:

- `я уже зарегистрирован`
- `я уже зарегестрирован`
- `я уже зареган`
- `я уже водитель`
- `я в вашем парке`

When detected, backend opens an existing-driver support menu.

That menu currently maps roughly to:

1. payouts
2. Yandex Pro / park visibility
3. tariffs
4. change car/documents/profile
5. manager

State for this menu is stored in `support_context_json`, for example:

- `mode = existing_driver_support`
- `menu = existing_driver_main`

## 10. FAQ / Support / Help Classification

### Fast FAQ classifier

`classify_dialog_intent(...)` in [app/dialog/faq.py](C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/app/dialog/faq.py) is a large rule-based intent classifier.

It checks for:

- human operator request
- existing driver support
- application status
- Yandex Pro problem
- tariff support
- driver profile update
- SMZ / employment-type situations
- greeting
- generic support question
- fallback to registration

### Support question detector

`looks_like_support_question(...)` decides whether text sounds like a question/help request.

It uses:

- punctuation
- helper words
- question starters
- Russian and Kazakh support markers

### Greeting detector

`looks_like_greeting(...)` handles:

- привет
- здравствуйте
- салам
- alo/allo
- English greetings
- Kazakh greeting markers

## 11. AI Layer: Real Behavior

The most important truth:

`AIService.respond(...)` is backend-first.

### AI decision pipeline

1. `DeterministicAIProvider.respond(...)` runs first.
2. If deterministic result is strong enough, it returns immediately.
3. LLM only helps in limited cases:
   - FAQ assistant when enabled
   - full LLM mode for completed state
4. If LLM fails, backend falls back safely.

### What deterministic AI handles

Inside `DeterministicAIProvider.respond(...)`, it handles:

- empty message clarification
- existing driver support
- mixed field + support messages
- greeting with support
- plain greeting
- confirm/field edit parsing
- support-only topics in `NEW`
- onboarding intent in `NEW`
- support during registration
- step help
- state-specific extraction/validation

So the deterministic provider is not small. It is the main decision engine.

### When LLM is used

LLM is used if:

- provider is configured
- FAQ assist is enabled
- backend thinks it might help answer FAQ/support

Providers supported:

- OpenAI
- Gemini

If provider config is missing, system falls back to deterministic AI.

## 12. Prompt Architecture

Prompt builders live in [app/dialog/llm_prompt.py](C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/app/dialog/llm_prompt.py).

There are two prompt families.

### A. FAQ assist prompt

Functions:

- `build_faq_assist_system_prompt()`
- `build_faq_assist_user_prompt(...)`

Purpose:

- answer only from knowledge base
- do not invent facts
- do not collect form fields
- do not change registration step
- if KB has no answer, redirect to office
- return only JSON with `reply`

This is intentionally narrow.

### B. Main structured decision prompt

Functions:

- `build_system_prompt()`
- `build_user_prompt(...)`

Purpose:

- force structured JSON output
- explain state machine rules
- explain allowed `next_state`
- include current prompt
- include already collected driver/vehicle data
- include knowledge base
- explain support-intent priority
- explain field-edit vs correction vs registration
- explain formatting rules for phone/date/license/model

### What the main prompt emphasizes

The main prompt explicitly says:

- do not invent data
- do not skip steps
- support intents are above registration intent
- use `faq/help` for park questions
- use `confirmation` for confirmation
- use `field_edit` when changing a field with a new value
- use `clarification` when unsure
- normalize:
  - dates -> `YYYY-MM-DD`
  - phone -> international with plus
  - driver license number -> keep spacing

It also contains domain-specific instructions:

- model name should be like `Camry`, not body code like `w221`
- rental answer: no rental cars, only drivers with their own cars

## 13. Intent Priority Model

This is one of the most important sections.

The actual priority is:

1. Hard engine branches
2. Priority interrupts
3. Existing driver support / support menu logic
4. Document/media routing
5. Active registration state handling
6. Deterministic AI classification
7. Optional LLM FAQ assist
8. Full LLM mode in limited cases

### Business priority rules already encoded

Support is generally above registration when support intent is clear.

Examples:

- existing driver support beats new registration
- human operator request beats normal flow
- Yandex problem beats passive onboarding
- support during active registration may repeat current question if interruption is not allowed

### Special nuance

Even if AI returns support/help, active registration may continue if backend decides support should not interrupt current step.

That logic exists around:

- `_is_active_flow(...)`
- `_should_interrupt_active_flow(...)`
- `_repeat_current_question(...)`

This is why some support-like messages during onboarding are answered but then followed by “current step” reminder.

## 14. Text-Step Fallback Parsing

Several fragile steps were moved out of pure AI dependence.

Important hardcoded fallback parsers in engine:

- city parsing for `ASK_CITY`
- address parsing for `ASK_ADDRESS`

Meaning:

If AI misses simple city/address answers, engine can still extract them directly and move forward.

This was added because real users saying `Астана` or `пр. Республики 12` were sometimes being rejected.

## 15. Step Help / “What do I do now?”

There are two layers for this:

1. generalized format wrapper in `format_in_flow_reply(...)`
2. explicit per-step help in `_step_instruction_reply(...)`

Current behavior:

- step instructions are now clearer
- examples were added for:
  - full name
  - phone
  - city
  - address
  - brand
  - model
  - year
  - plate
  - color

This was added because drivers often did not understand what exact value to send next.

## 16. Document Handling

Document logic is handled by `_handle_document(...)` in [app/dialog/engine.py](C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/app/dialog/engine.py).

### Important current behavior

The engine distinguishes media context:

- real registration document context
- text registration context
- support context
- existing-driver support context

This matters because earlier the bot tried to OCR documents even when it was actually waiting for text like city or address.

That caused annoying replies like:

- “photo received but document type not determined”

even when the real problem was wrong flow state.

This was fixed by splitting media context and responding differently during text steps.

### Document type slots

Document slots:

- `driver_license_front`
- `driver_license_back`
- `id_card`
- `vehicle_registration_doc`

OCR can also detect:

- `selfie_with_license`
- `unknown`

### Combined upload behavior

The flow supports recognizing multiple document types from one upload:

- `additional_document_types`
- `contains_both_license_sides`
- PDF from eGov/Kaspi with both sides on one page

`expand_uploaded_document_types(...)` can mark multiple document slots as satisfied from one file.

This is the basis for the “one upload can satisfy multiple steps” behavior.

## 17. Document Extraction / OCR

OCR logic lives in [app/documents/extraction.py](C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/app/documents/extraction.py).

### Feature flag

Extraction is enabled only if:

- `document_extraction_enabled` is true
- and an API key exists

So yes: the env flag matters.

### Current provider

Document extraction currently uses Gemini if configured.

If Gemini fails or is unavailable:

- result becomes empty
- no strong OCR fallback exists in this service

### OCR prompt purpose

The extraction prompt tells the model to:

- identify document type
- detect if both license sides are present
- detect additional document types
- extract known registration fields
- return JSON only
- never invent
- normalize dates to `YYYY-MM-DD`
- return empty fields for selfie

### Normalization layer after OCR

Raw OCR output is not applied directly.

`normalize_extracted_fields(...)`:

- cleans full name
- splits full name into parts
- normalizes IIN
- parses dates
- normalizes driver license number
- normalizes car brand/model
- normalizes plate number
- normalizes registration certificate
- normalizes VIN
- ignores low-confidence junk
- ignores selfie fields

So OCR has a second backend filter before touching driver data.

## 18. Registration Confirmation and Submission

Once all required fields are collected:

- next state becomes `CONFIRM_DATA`
- user confirms
- backend moves to submission

Submission service:

- [app/integrations/yandex/service.py](C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/app/integrations/yandex/service.py)

### Submission flow

`YandexSubmissionService.submit(...)`:

1. maps driver to Yandex payload
2. validates payload
3. chooses submission scenario:
   - bind existing driver + vehicle
   - submit vehicle and bind to existing driver
   - submit full new driver
4. handles partial submission errors
5. stores Yandex IDs and statuses

### Validation before Yandex

The service validates:

- required driver fields
- required vehicle fields
- date consistency
- IIN
- driver license
- hired date
- employment type warnings
- document ref presence
- Yandex catalog brand/model pairing

If validation fails:

- backend raises error before Yandex call
- user gets formatted validation error reply

### Existing driver sync

There is also:

- `find_and_sync_existing_driver(...)`

This allows finding a driver profile in Yandex and syncing local state back into the bot DB.

That is one of the key pieces behind “find driver and load info” scenarios.

## 19. Support Flow Engine

Support flows are not only manager escalation.

There is a mini step-by-step support engine in `engine.py`.

Topics include:

- `yandex_login`
- `yandex_sms`
- `account_inactive`
- `go_online`

### How support flow works

`_handle_support_flow(...)`:

1. detects support topic
2. starts topic if it is new
3. sends intro + current step
4. waits for progress/probem words
5. if user says done -> next step
6. if user reports problem -> escalate to manager
7. if all steps complete -> mark support flow completed

This is a guided troubleshooting flow, not just FAQ.

## 20. Stateful Support Menus

There is another layer:

- `_handle_stateful_support_menu(...)`

This handles multi-step menu interactions stored in `support_context_json`.

Examples:

- existing driver menu
- driver lookup mode
- profile update mode

This is why certain replies like `1`, `2`, `3`, `4`, `5` can mean totally different things depending on active support context.

## 21. Traces, Debug, and Observability

### AI trace

Every AI decision can be stored with:

- provider
- intent
- confidence
- next_state
- reply preview
- extracted fields
- normalized fields
- reasoning summary
- fallback info
- validation errors
- final decision payload

This is written through `_record_ai_trace(...)`.

### System trace

Non-AI decisions also get traced with `_record_system_trace(...)`.

That covers:

- special commands
- menu routing
- support branch decisions
- priority interrupt branches

### Registration debug trace

There is an additional granular event:

- `registration_debug_trace`

It records:

- state before
- message type
- media context
- detected document type
- extracted fields
- state after
- submit called yes/no
- mime type
- debug source

This was added specifically to stop guessing why a registration failed.

Important debug sources now include things like:

- `active_flow_text`
- `text_step_applied`
- `text_step_media`
- `document_type_not_determined`
- `document_processed`
- `submit_attempt`

## 22. Conversation Events

Business events provide a second observability layer separate from AI traces.

Examples:

- onboarding started
- support flow started
- support escalated
- support completed
- existing driver menu opened
- document-related events
- Yandex login confirmed

These events are useful for:

- admin panel history
- exports
- debugging user journeys
- analytics

## 23. Where Real-World Bugs Usually Come From

Based on the current code and the live issues already seen, bugs typically come from these categories.

### A. Wrong branch wins too early

Examples:

- greeting detected, but real intent was support
- support detected, but real intent was registration
- existing driver intent detected too late

### B. State and message mismatch

Examples:

- user sends document while backend expects city
- user sends city while backend still thinks another step is active

### C. OCR recognized data, but flow did not move correctly

This is often not OCR failure itself, but state transition failure.

### D. User wording drift

Examples:

- typo variants
- slang like `зареган`
- Kazakh/Russian mixes
- bare `1`, `2`, `3`

### E. Menu context ambiguity

Same symbol like `1` can mean:

- greeting menu registration
- existing-driver payout menu
- support follow-up option

Only backend context resolves that.

### F. Yandex sync vs local driver mismatch

Sometimes “can’t find driver” is not purely a chatbot problem; it can be:

- wrong phone
- wrong WhatsApp number
- missing IIN
- no synced Yandex profile

## 24. Current Important Business Rules

These are hardcoded or strongly implied in prompts and logic.

### Registration

- start from `NEW`
- do not skip steps without reason
- documents can prefill data
- manual mode can skip waiting for docs

### Rental

Current rule:

- no rental cars now
- only drivers with their own cars

### Support

- existing drivers should go to support, not restart onboarding
- support may escalate to manager

### Yandex Pro

- after successful registration, user must complete Yandex Pro login
- completed state is not just “data sent”; post-submit follow-up matters

## 25. Config / Feature Flags That Matter

Important settings inferred from code:

- AI provider selection
- OpenAI API key
- Gemini API key
- `llm_mode`
- `llm_faq_assist_enabled`
- `document_extraction_enabled`
- public office/site address
- Yandex-related settings

The most important one for OCR is:

- `DOCUMENT_EXTRACTION_ENABLED=true`

Without that and a valid provider key, photo auto-extraction does not really work.

## 26. What Is AI vs What Is Not AI

### Definitely not AI

- state machine order
- support context menus
- Yandex payload validation
- registration progression order
- document slot satisfaction
- application/driver DB writes
- final submission

### AI-assisted but backend-controlled

- classify intent
- recognize FAQ/help/support
- parse registration field from natural text
- answer KB-based questions through LLM FAQ assist
- OCR through Gemini

### AI does not own the final truth

Even when AI returns something, backend may:

- ignore it
- repeat current step
- keep same state
- override it with direct parser
- escalate instead

## 27. Practical Mental Model

If you want the shortest accurate mental model:

1. `engine.py` is the traffic cop.
2. `registration_flow.py` is the step order.
3. `faq.py` is the fast intent/topic classifier.
4. `ai.py` is a backend-first extraction/classification layer with optional LLM assist.
5. `llm_prompt.py` defines what the model is allowed to do.
6. `extraction.py` handles OCR for docs.
7. `yandex/service.py` validates and submits the final payload.

## 28. Recommended Reading Order in Code

If someone new needs to understand the bot fast, read in this order:

1. [app/dialog/engine.py](C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/app/dialog/engine.py)
2. [app/documents/registration_flow.py](C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/app/documents/registration_flow.py)
3. [app/dialog/faq.py](C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/app/dialog/faq.py)
4. [app/dialog/ai.py](C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/app/dialog/ai.py)
5. [app/dialog/llm_prompt.py](C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/app/dialog/llm_prompt.py)
6. [app/documents/extraction.py](C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/app/documents/extraction.py)
7. [app/integrations/yandex/service.py](C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/app/integrations/yandex/service.py)

## 29. Summary

The current bot is:

- not an LLM chatbot
- a backend-controlled registration/support system
- stateful
- heavy on deterministic routing
- AI-assisted for extraction, FAQ, and OCR
- vulnerable mainly at routing boundaries and messy real-user phrasing

The most important operational truth is this:

When the bot behaves stupidly, the cause is usually not “AI is dumb”.

Usually it is one of:

- wrong branch priority
- wrong active state
- missing synonym/typo handling
- context menu mismatch
- OCR output not aligned with state progression
- Yandex/local profile mismatch

That is why most of the fixes already made were not “better prompting”, but backend routing corrections.
