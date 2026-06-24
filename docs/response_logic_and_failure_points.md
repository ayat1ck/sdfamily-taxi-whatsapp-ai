# Response logic, prompts, priorities, documents, and failure points

This document describes what currently controls bot answers in the FastAPI microservice, what has priority, how message and document types are detected, how photo/PDF recognition works, and where the system is most likely to break.

Main source files:

- `app/whatsapp/webhook.py` - public WhatsApp webhook.
- `app/whatsapp/parser.py` - converts Meta payloads into internal message objects.
- `app/dialog/engine.py` - main backend state machine and priority routing.
- `app/dialog/ai.py` - deterministic answer logic, FAQ matching, optional LLM fallback.
- `app/dialog/llm_prompt.py` - prompts for full LLM and FAQ assistant.
- `app/documents/extraction.py` - Gemini document/photo/PDF recognition.
- `app/documents/registration_flow.py` - document order and next registration step.
- `app/config.py` - feature flags, model/provider settings, tokens.

## 1. Webhook entry point

Production URL:

```text
https://sdfamily-taxi-app.onrender.com/webhooks/whatsapp
```

The same path has two methods:

- `GET /webhooks/whatsapp` verifies the Meta webhook by checking `hub.verify_token` against `WHATSAPP_VERIFY_TOKEN` and returning `hub.challenge`.
- `POST /webhooks/whatsapp` receives incoming WhatsApp events.

POST flow:

1. Read JSON payload from Meta.
2. Parse messages with `parse_whatsapp_payload(...)`.
3. For every parsed message, find or create a driver by WhatsApp phone.
4. If driver mode is `manual`, `paused`, or `closed`, the bot stores the incoming message, marks the chat as requiring attention, and does not auto-answer.
5. Otherwise, `DialogueEngine.handle_message(...)` decides the reply.
6. The reply is sent through WhatsApp Cloud API.
7. Incoming/outgoing messages and integration job status are saved.

Important weak point: webhook sender authenticity is not validated with Meta `X-Hub-Signature-256`. Anyone who can reach the endpoint can POST a fake WhatsApp-shaped payload unless Render/network rules or another layer protects it.

## 2. Who is higher: backend or AI

The backend is higher than AI.

The current architecture is explicitly backend-first:

```text
WhatsApp payload
-> parser
-> DialogueEngine
-> deterministic AI/provider rules
-> optional LLM only in narrow cases
-> backend applies fields, state transitions, duplicates, Yandex validation
-> WhatsApp reply
```

`AIService.respond(...)` first calls `DeterministicAIProvider.respond(...)`.

The deterministic backend result immediately wins when intent is one of:

- `registration`
- `confirmation`
- `correction`
- `field_edit`
- `faq` with a non-empty reply
- `help` with a non-empty reply

The LLM can be used only after deterministic logic does not produce a strong answer.

By default in `app/config.py`:

```text
AI_PROVIDER=openai
LLM_MODE=faq_only
LLM_FAQ_ASSIST_ENABLED=false
OPENAI_MODEL=gpt-4o-mini
GEMINI_MODEL=gemini-2.5-flash
DOCUMENT_EXTRACTION_ENABLED=true
```

That means normal dialogue answers are mostly deterministic. The general full LLM route is only used when:

- `llm_mode == "full"`
- current state is `COMPLETED`
- an LLM provider is configured

FAQ LLM assistant is only used when:

- `llm_faq_assist_enabled == true`
- a provider key exists
- deterministic logic decides the message might need FAQ help

If LLM fails, the system falls back to deterministic backend output.

## 3. What prompt controls answers

There are two main prompt families.

### Full dialogue prompt

Defined in `build_system_prompt()` and `build_user_prompt()` in `app/dialog/llm_prompt.py`.

The system prompt tells the model:

- You are an AI manager for SD Family Taxi in WhatsApp.
- Communicate only in Russian.
- Help with registration, Yandex Pro, and support after connection.
- Do not invent data.
- Return strict JSON by schema.
- State machine is mandatory.
- Do not jump between steps without a reason.
- If the user asks a park/office/conditions/documents/Yandex Pro question, treat it as FAQ/help and keep the current state.
- Do not repeat the registration question verbatim when the user asks a different question.
- Confirmation should be `confirmation`.
- Direct field change in `confirm_data` should be `field_edit`.
- If the user asks to change a field but does not give a new value, ask clarification.
- Use `registration` only when the message really answers the current step.
- Fill extracted fields only from explicit user text.
- Return dates as `YYYY-MM-DD`.
- Return phone in international format with `+`.
- If unsure, clarification is better than wrong data.

The user prompt includes:

- current state;
- dialogue mode;
- allowed next states;
- current required question;
- already collected driver and vehicle fields;
- driver message;
- knowledge base contents;
- instruction to either extract current-step data, answer FAQ, or return field edit.

Weak point: this prompt is not always active. In current default settings, full LLM is usually not controlling registration. Deterministic rules are.

### FAQ assistant prompt

Defined in `build_faq_assist_system_prompt()` and `build_faq_assist_user_prompt()`.

It tells the model:

- You are a WhatsApp helper for SD Family Taxi.
- Only answer driver questions from the knowledge base.
- Speak only Russian, briefly and directly.
- Do not invent facts, numbers, addresses, or conditions.
- Do not collect registration form data.
- Do not change registration step.
- If the knowledge base has no answer, invite the driver to the SD Family Taxi office and use the address from `park_info`.
- Return JSON: `{"reply": "..."}`.

Weak point: if knowledge base text is outdated, incomplete, or mojibake/corrupted, the LLM assistant can only answer from bad context.

## 4. Answer priority order in `DialogueEngine`

For every incoming message, the backend checks conditions in this order:

1. Save incoming message and load current driver state.
2. If state is `duplicate_rejected` and message is file/image/unsupported, repeat duplicate rejection reply.
3. If message type is `unsupported`, answer that only text, image, and document are supported.
4. If message type is `image` or `document`, go to document handler immediately.
5. Handle special commands, for example restart/delete/status-like commands.
6. Handle priority interrupts, for example manager/operator request, existing driver intent, Yandex support-like messages.
7. If user wants manual data entry while the bot expects a data document, switch to manual data path.
8. If there is a pending field edit in `confirm_data` or `yandex_error`, treat the next text as the new value for that field.
9. If state is `completed`, use registered-driver support flow.
10. If state is a Yandex Pro follow-up state, use Yandex Pro support flow.
11. If state is `new`, call `AIService.respond(...)` and decide whether to start onboarding or answer FAQ/help.
12. For normal registration states, call `AIService.respond(...)`.
13. Apply extracted fields.
14. Check duplicate IIN/plate.
15. Move state forward.
16. Before sending to Yandex, validate all required Yandex fields.
17. Send or show errors/confirmation.

Important: image/document messages bypass text AI. They go straight to `_handle_document(...)`.

## 5. How message types are determined

`app/whatsapp/parser.py` reads Meta payload:

```text
message.type == "text"      -> internal message_type="text"
message.type == "image"     -> internal message_type="image"
message.type == "document"  -> internal message_type="document"
anything else               -> internal message_type="unsupported"
```

For text:

- `text.body` becomes internal `text`.
- Meta message id is saved.
- raw message payload is saved.

For image/document:

- media id is taken from `message.image.id` or `message.document.id`.
- MIME type is taken from media payload.
- filename is taken from document payload or defaults to `image.bin` / `document.bin`.

Weak points:

- Audio, sticker, location, contact, button, interactive replies are all `unsupported`.
- Captions on images/documents are not extracted or used.
- Multiple messages in one webhook are processed sequentially, but there is no strong idempotency guard visible here for repeated Meta deliveries.
- If Meta sends a status update without `messages`, parser returns no messages and nothing happens.

## 6. How photos/PDFs are scanned

Document/photo handling lives in `_handle_document(...)`.

Flow:

1. If driver is in a Yandex Pro follow-up state, the file is not OCR-scanned for registration. It is marked for manager review.
2. Otherwise the service tries to download the media from WhatsApp using `WhatsAppMediaClient.fetch_media(media_id)`.
3. If download fails, it logs a warning but continues.
4. If document extraction is enabled and bytes exist, `DocumentExtractionService.extract(...)` is called.
5. Gemini receives the bytes plus extraction prompt.
6. Gemini returns JSON with:
   - `document_type`
   - recognized fields
   - confidence
   - `contains_both_license_sides`
   - `additional_document_types`
7. If Gemini detects a document type different from expected, backend may use detected type when current state is not already a fixed document state.
8. The uploaded item is stored as `stored_in_whatsapp` with WhatsApp media id, filename, MIME type, and message id.
9. Extracted fields are normalized and applied to driver/vehicle.
10. Next registration state is recalculated.
11. Bot replies with accepted document type and recognized fields, or says data will be filled manually in later steps.

Important: current code stores WhatsApp media ids, not Google Drive files, in the normal document path. There is a separate upload helper, but `_handle_document(...)` uses `storage_provider="whatsapp"`.

## 7. How the system determines what is on the photo

There are two layers:

### Layer 1: expected state

If the driver is currently in a document state, backend trusts the state first:

```text
ask_driver_license_front        -> driver_license_front
ask_driver_license_back         -> driver_license_back
ask_id_card                     -> id_card
ask_vehicle_registration_doc    -> vehicle_registration_doc
ask_selfie_with_license         -> selfie_with_license
```

If current state is one of these fixed states, uploaded document type is taken from the state, even if Gemini detected another type.

### Layer 2: Gemini detected type

If current state is a broader registration state, backend may use Gemini's `document_type` if it is not already uploaded.

Allowed recognized document types:

- `driver_license_front`
- `driver_license_back`
- `id_card`
- `vehicle_registration_doc`
- `selfie_with_license`
- `unknown`

PDF special case:

- If the uploaded item is a PDF and the primary type is one side of the driver's license, the system marks both front and back as uploaded.
- If Gemini says `contains_both_license_sides=true`, both sides are also marked uploaded.

Weak point: in a fixed document state, a wrong photo can be accepted as the expected document. OCR may detect another type, but storage still uses the state-selected document type.

## 8. What fields can OCR extract

From photo/PDF, Gemini can return:

- `full_name`
- `iin`
- `birth_date`
- `address`
- `driver_license_number`
- `driver_license_issue_date`
- `driver_license_expires_at`
- `driving_experience_since`
- `brand`
- `model`
- `year`
- `plate_number`
- `color`
- `registration_certificate`
- `vin`

Normalization rules:

- full name must have at least two parts; then it is split into last/first/middle names.
- IIN must be exactly 12 digits and pass Kazakhstan IIN validation.
- Dates are parsed and converted to normalized date format.
- license number, plate number, registration certificate, brand, model are normalized by validators.
- VIN is accepted if it has at least 11 characters after whitespace removal.
- selfie with license returns no extracted fields by design.
- if confidence is below `0.35` and nothing was recognized, fields are ignored.

Weak point: if Gemini returns a plausible but wrong field with confidence above threshold, backend can apply it automatically unless validators catch it.

## 9. Registration state order

The normal text/data order is:

1. `ask_full_name`
2. `ask_phone`
3. `ask_city`
4. `ask_address`
5. `ask_iin`
6. `ask_birth_date`
7. `ask_driving_experience_since`
8. `ask_car_brand`
9. `ask_car_model`
10. `ask_car_year`
11. `ask_car_plate`
12. `ask_car_color`
13. `ask_car_registration_certificate`
14. `ask_driver_license_number`
15. `ask_driver_license_issue_date`
16. `ask_driver_license_expires_at`
17. `ask_employment_type`
18. `ask_hired_at`
19. `ask_hearing_impaired`
20. `confirm_data`

Document sequence:

1. `ask_driver_license_front`
2. `ask_driver_license_back`
3. `ask_id_card`
4. `ask_vehicle_registration_doc`
5. `ask_selfie_with_license`

`next_registration_state(...)` first checks missing documents unless manual data entry was chosen. Then it checks missing text fields. Selfie is still required before final confirmation.

## 10. What decides FAQ/help vs registration

Deterministic logic uses many keyword and validator functions:

- greetings;
- onboarding intent;
- support-only topics;
- FAQ matching from knowledge base;
- mixed "field + support question";
- current registration step validators;
- confirmation parsing;
- field edit parsing;
- car brand/model catalog resolution;
- Yandex Pro issue keywords;
- operator/manager request keywords;
- duplicate/existing driver intent.

Only if deterministic logic cannot answer confidently can optional LLM FAQ assist run.

Weak point: keyword-based routing is brittle. Small wording differences, slang, typos, Kazakh/Russian mix, transliteration, or voice-transcribed text can route to the wrong branch.

## 11. Backend validations and safeguards

The backend does enforce several safeguards:

- unsupported message types are rejected;
- manual/paused/closed chats do not auto-answer;
- duplicate IIN is checked;
- duplicate plate number is checked;
- Yandex validation is run before sending;
- field edits are normalized and validated;
- dates/phone/IIN/plate/STS/license formats are validated;
- car brand/model can be checked against Yandex catalog;
- AI next states are constrained by backend allowed states in full LLM mode;
- AI traces are recorded for inspection.

Weak point: some safeguards depend on validators and catalog coverage. If a validator is too permissive or too strict, the bot either saves bad data or blocks good data.

## 12. Highest-risk failure points

### 1. Meta webhook security

The service verifies only the initial GET token. POST request signatures are not visibly validated.

Impact: fake inbound messages could be injected into the system.

### 2. WhatsApp media retention

Documents are stored with WhatsApp media id and `storage_provider="whatsapp"`.

Impact: if WhatsApp media URLs expire or media ids cannot be fetched later, admins may lose access to original files unless they were separately stored.

### 3. OCR hallucination or wrong read

Gemini is instructed not to invent values, but it can still misread document text.

Impact: wrong IIN, license number, dates, plate, VIN, or car data can be saved automatically.

### 4. Wrong photo accepted in fixed document state

If the bot asks for ID card but user sends another document, backend may still store it as ID card because current state has priority.

Impact: registration moves forward with the wrong document slot filled.

### 5. Captions ignored

Images/documents can include useful captions from the driver, but parser does not preserve captions.

Impact: "это не права, это техпаспорт" or correction text attached to a file is ignored.

### 6. Unsupported interactive WhatsApp types

Buttons, list replies, audio, contact cards, locations, stickers are unsupported.

Impact: real user behavior can get a generic unsupported reply.

### 7. AI configuration confusion

There are multiple modes:

- deterministic backend;
- FAQ-only LLM assistant;
- full LLM only in `COMPLETED`;
- Gemini OCR.

Impact: changing env vars may not affect answers the way expected. For example, changing prompt may not change registration if deterministic logic still wins.

### 8. Knowledge base drift

FAQ answers depend on files in `knowledge_base/`.

Impact: outdated office address, conditions, payout info, document requirements, or Yandex instructions lead to wrong answers.

### 9. Mojibake / encoding risk

Some source text currently appears mojibake in terminal output.

Impact: if the deployed files are actually corrupted rather than display-only corrupted, bot replies/prompts/keyword matching can break badly.

### 10. Idempotency/retry risk

Meta can retry webhook deliveries. The parser/handler saves and processes messages, but there is no obvious global dedupe check by provider message id in the visible webhook flow.

Impact: duplicate messages can trigger duplicate replies or repeated state transitions.

### 11. Sequential multi-message webhook risk

If several messages arrive in one webhook, they are processed in order in one request.

Impact: a file and text correction sent together can be interpreted differently than expected, especially because file messages bypass text AI.

### 12. Manual mode dependency

If a driver is `manual`, `paused`, or `closed`, backend does not auto-answer.

Impact: users can appear "ignored" if manager forgets to resume automation.

### 13. Yandex/catalog dependency

Brand/model validation and Yandex submission depend on external API/config.

Impact: wrong/missing Yandex env vars or catalog API failures can block registration at the end.

### 14. Google Sheets sync side effect

Every bot response tries to sync application to Google Sheets if settings exist.

Impact: Google failure is logged, but slow or repeated failures can create latency/noise.

### 15. Document extraction availability

OCR requires:

- `DOCUMENT_EXTRACTION_ENABLED=true`
- `GEMINI_API_KEY` or `OPENAI_API_KEY`
- currently Gemini library/provider path for actual extraction

Impact: if Gemini key/package is missing, files are accepted but fields are not recognized, forcing manual text steps.

### 16. Low confidence threshold

OCR ignores fields only when confidence is below `0.35` and no fields were recognized.

Impact: a bad extraction with some recognized fields may still be applied.

### 17. State machine mismatch

The bot decides next state from saved fields/documents. If data is partially filled by OCR or manager edit, it can skip questions.

Impact: users may not be asked to verify a field until `confirm_data`.

### 18. Field edit ambiguity

Field edit parsing is keyword-based.

Impact: "поменял машину" may be treated as support/data-change intent or as a specific field edit depending on wording and state.

### 19. Registered-driver support vs new registration

In `completed`, support flow has priority. In `new`, onboarding/support detection decides whether to start registration.

Impact: existing drivers using a new WhatsApp number can be routed as new applicants unless duplicate IIN/plate catches them later.

### 20. Lack of human review gate for OCR fields

OCR fields are applied immediately and only later shown at confirmation.

Impact: wrong OCR can propagate into Google Sheets/Yandex validation unless user notices in confirmation.

## 13. Best fixes by priority

1. Add Meta POST signature validation with `X-Hub-Signature-256`.
2. Add idempotency by Meta `message.id` before processing.
3. Store uploaded documents permanently in Google Drive/S3, not only WhatsApp media id.
4. Preserve and process media captions.
5. In fixed document states, if OCR detects a different document type, ask confirmation instead of blindly accepting.
6. Add OCR confidence thresholds per field and require confirmation before applying high-risk fields: IIN, license number, dates, plate, VIN.
7. Add admin-visible OCR trace: raw Gemini JSON, detected type, confidence, applied fields, ignored fields.
8. Add tests for routing priority: unsupported, file, manual, paused, new, completed, yandex follow-up, field edit.
9. Add tests for webhook retry duplicate message id.
10. Normalize all source files to UTF-8 and verify deployed Russian text is not corrupted.
11. Add support for WhatsApp interactive replies/buttons and media captions.
12. Make env mode visible in `/health` or admin integrations page: provider, `llm_mode`, FAQ assist, document OCR enabled, configured/missing keys.
13. Add a manager review queue for suspicious documents and low-confidence OCR.
14. Version knowledge base content and show "last updated" in admin.
15. Separate "answering prompt" docs from deterministic backend rules so future prompt edits do not create false expectations.

## 14. Practical mental model

If a text reply looks wrong, check in this order:

1. Driver state.
2. Driver dialog mode: `auto`, `manual`, `paused`, `closed`.
3. Whether the message was text or file.
4. Special command / priority interrupt branch.
5. Deterministic AI trace: intent, next state, extracted fields, reasoning summary.
6. FAQ knowledge base match.
7. LLM mode/env vars only after deterministic routing is understood.

If a photo/document result looks wrong, check:

1. Current driver state at upload time.
2. WhatsApp media id and MIME type.
3. Whether media download succeeded.
4. Whether document extraction was enabled.
5. Gemini detected `document_type`.
6. Stored document type chosen by backend.
7. Raw extracted fields and normalized applied fields.
8. Duplicate checks for IIN/plate.
9. Next registration state after upload.

