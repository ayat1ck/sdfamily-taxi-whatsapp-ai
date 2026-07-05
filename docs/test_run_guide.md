# n8n test run guide

Date: 2026-06-24

This guide is the shortest practical path to run the migration layer in n8n and verify the first happy-path and escalation cases.

## 1. Import both workflows

Import these files into n8n:

- [n8n/workflows/whatsapp_incoming_router.json](/C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/n8n/workflows/whatsapp_incoming_router.json)
- [n8n/workflows/manager_escalation.json](/C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/n8n/workflows/manager_escalation.json)

### Import steps

1. Open n8n.
2. Click `Import from File`.
3. Import `manager_escalation.json` first.
4. Import `whatsapp_incoming_router.json` second.
5. Save both workflows.

## 2. Create PostgreSQL credential

Create one PostgreSQL credential in n8n and attach it to every PostgreSQL node.

### Recommended credential name

- `Postgres SD Family Taxi`

### Fields to fill

- host
- port
- database
- user
- password
- SSL settings if required

### What to check

- n8n can connect to the database;
- the credential works from the workflow editor;
- all PostgreSQL nodes show the selected credential.

## 3. Set environment variables

Set these env vars in the n8n runtime:

- `OPENAI_API_KEY`
- `AI_ROUTER_MODEL`
- `AI_ROUTER_PROMPT`
- `AI_ROUTER_SCHEMA_JSON`
- `OPENAI_RESPONSES_URL`
- `WHATSAPP_ACCESS_TOKEN`
- `WHATSAPP_PHONE_NUMBER_ID`

### Suggested values

- `OPENAI_RESPONSES_URL=https://api.openai.com/v1/responses`
- `AI_ROUTER_MODEL=gpt-4o-mini`

### Important

- `AI_ROUTER_PROMPT` must contain the full prompt from [docs/ai_router_prompt.md](/C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/docs/ai_router_prompt.md).
- `AI_ROUTER_SCHEMA_JSON` must contain the compact JSON from [docs/ai_router_schema.json](/C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/docs/ai_router_schema.json).

## 4. Execute SQL schema

Run the SQL from [docs/postgres_schema.md](/C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/docs/postgres_schema.md) in your PostgreSQL database.

### Tables that must exist

- `driver_states`
- `conversation_messages`
- `manager_tickets`

### Minimum check after SQL

Verify these tables exist before enabling the workflow.

## 5. Enable the workflows

### Main workflow

Enable `whatsapp_incoming_router`.

### Escalation workflow

Enable `manager_escalation`.

### Before enabling

- confirm PostgreSQL credentials are attached;
- confirm env vars are present;
- confirm the AI Router node can read `AI_ROUTER_SCHEMA_JSON`;
- confirm the WhatsApp send node has `WHATSAPP_ACCESS_TOKEN` and `WHATSAPP_PHONE_NUMBER_ID`.

## 6. Send the first test webhook payload

Use any of the payloads from [docs/test_payloads.md](/C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/docs/test_payloads.md).

### Recommended first payload

Start with:

- `Здравствуйте`

### How to send

Option 1:

- use n8n test webhook URL;
- paste the JSON payload into Postman or curl;
- send a POST request.

Option 2:

- if webhook is publicly reachable, send directly to the production-like URL.

## 7. Check `driver_states`

After the webhook runs, inspect `driver_states`.

### Verify for a normal greeting

- a row exists for the phone number;
- `state` is not empty;
- `dialog_mode` is `auto`;
- `last_intent` is set;
- `last_message_at` is updated;
- `requires_attention` is `false`.

### Verify after escalation

- `dialog_mode` becomes `manual`;
- `requires_attention` becomes `true`;
- `last_intent` reflects the escalation intent.

## 8. Check `conversation_messages`

After the webhook runs, inspect `conversation_messages`.

### Verify

- inbound customer message is inserted;
- outgoing bot message is inserted;
- `delivery_status` is `pending` or `sent` depending on workflow stage;
- the text body is stored correctly;
- UTF-8 text remains readable.

## 9. Check `manager_tickets`

Send an escalation message and then inspect `manager_tickets`.

### Verify

- one open ticket exists;
- `phone` matches the sender;
- `topic` matches the escalation topic;
- `reason` is populated;
- `status` is `open`;
- `priority` is set.

## 10. Run these 3 tests first

### Test 1: "Здравствуйте"

#### Expected result

- intent should usually be `faq`, `unknown`, or a safe greeting-related reply depending on the AI prompt;
- the bot should reply in UTF-8 without mojibake;
- `driver_states` row should be created or updated;
- `conversation_messages` should contain inbound and outbound rows;
- no manager ticket should be created.

#### What matters most

- the workflow completes end-to-end;
- the reply is visible and readable;
- no SQL or HTTP node fails.

### Test 2: "Оператор"

#### Expected result

- AI returns `human_operator` or a low-risk escalation equivalent;
- `manager_tickets` gets a new ticket;
- `driver_states.dialog_mode = manual`;
- `driver_states.requires_attention = true`;
- the bot replies exactly:
  - `Ваш запрос передан менеджеру. Ожидайте ответа.`

#### What matters most

- escalation is durable in PostgreSQL;
- future automation is blocked by manual mode.

### Test 3: "Осы такса паркке тіркелейін деп едім"

#### Expected result

- intent should be `registration`;
- the workflow should not escalate to manager by default;
- `driver_states` should remain in auto mode unless your prompt or rules force escalation;
- `conversation_messages` should record the exchange;
- the reply should be a registration-oriented prompt or clarification.

#### What matters most

- Kazakh text is not broken;
- UTF-8 stays correct in input and output;
- the AI router classifies the phrase as registration.

## 11. If OpenAI returns invalid JSON

If the AI Router response cannot be parsed:

1. Check `OPENAI_API_KEY`.
2. Check that `AI_ROUTER_PROMPT` is not truncated.
3. Check that `AI_ROUTER_SCHEMA_JSON` is valid JSON.
4. Check the HTTP Request node response body in n8n execution logs.
5. Confirm the workflow routes to `manager_escalation`.
6. Confirm a `manager_tickets` row is created.

### Immediate fallback rule

If the AI response is invalid, do not try to continue the normal flow.

Use escalation as the safe default.

## 12. If WhatsApp API did not send the reply

If the reply is not delivered:

1. Check `WHATSAPP_ACCESS_TOKEN`.
2. Check `WHATSAPP_PHONE_NUMBER_ID`.
3. Check the outgoing HTTP Request node status code.
4. Inspect the response body from WhatsApp Cloud API.
5. Confirm the recipient phone number format is correct.
6. Check network access from the n8n host.
7. Check whether the token has the correct permissions.

### Database check

- if the message was written to `conversation_messages` but not sent, keep the row for retry;
- if needed, set `delivery_status = failed` or add a retry mechanism later.

### Practical rule

Do not assume the bot failed just because the message was not sent.
First confirm whether the workflow reached the send node and what WhatsApp returned.

## 13. Minimal success definition

The first successful run is when all of these are true:

- workflow imports successfully;
- webhook receives a payload;
- PostgreSQL state is created or updated;
- AI returns a valid JSON classification;
- outbound reply is stored and sent;
- manager escalation creates a ticket when requested;
- no mojibake appears in Russian or Kazakh text.

