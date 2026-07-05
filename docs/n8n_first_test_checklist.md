# n8n first test checklist

Date: 2026-06-24

Use this checklist for the first end-to-end verification of the migration layer.

## 1. Import workflow

- Import [n8n/workflows/whatsapp_incoming_router.json](/C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/n8n/workflows/whatsapp_incoming_router.json)
- Import [n8n/workflows/manager_escalation.json](/C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/n8n/workflows/manager_escalation.json)

## 2. Connect PostgreSQL credentials

- Create a PostgreSQL credential in n8n.
- Attach it to all PostgreSQL nodes.
- Verify the database is reachable.

## 3. Set env vars

- `OPENAI_API_KEY`
- `AI_ROUTER_MODEL`
- `AI_ROUTER_PROMPT`
- `AI_ROUTER_SCHEMA_JSON`
- `WHATSAPP_ACCESS_TOKEN`
- `WHATSAPP_PHONE_NUMBER_ID`
- `OPENAI_RESPONSES_URL`

## 4. Test webhook through curl/Postman

- Send the payload from [docs/test_payloads.md](/C:/Users/ayat_/sdfamily-taxi-whatsapp-ai/docs/test_payloads.md) to the webhook URL.
- Start with the text payload `"Здравствуйте"`.
- Then test `"Оператор"`.

## 5. Check `driver_states`

- Confirm a row is created or updated for the test phone number.
- Confirm `dialog_mode` is `auto` for normal messages.
- Confirm `dialog_mode` becomes `manual` after escalation.
- Confirm `last_intent` is stored.

## 6. Check `conversation_messages`

- Confirm inbound messages are stored.
- Confirm outbound bot replies are stored.
- Confirm `delivery_status` updates after WhatsApp send.

## 7. Check WhatsApp reply

- Confirm the outgoing HTTP request succeeds.
- Confirm the reply body is UTF-8 and not mojibake.
- Confirm the message body matches the AI reply.

## 8. Check manager escalation

- Send `"Оператор"`.
- Confirm `manager_tickets` receives a row.
- Confirm `driver_states.dialog_mode = manual`.
- Confirm `driver_states.requires_attention = true`.
- Confirm the final reply says: `Ваш запрос передан менеджеру. Ожидайте ответа.`

## 9. Success criteria

- Workflow imports without structural errors.
- PostgreSQL writes succeed.
- AI router returns valid JSON.
- Happy-path reply is sent.
- Escalation path creates a manager ticket and stops auto handling.

