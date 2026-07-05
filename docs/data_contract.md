# SD Family Taxi data contract for n8n migration

Date: 2026-06-24

This document defines the payloads and fields that the n8n migration layer should use.

## 1. Input webhook payload

Source: WhatsApp Cloud API webhook.

Expected shape in practice:

```json
{
  "entry": [
    {
      "changes": [
        {
          "value": {
            "messages": [
              {
                "id": "wamid.xxx",
                "from": "77071234567",
                "type": "text",
                "text": {
                  "body": "Оператор"
                }
              }
            ],
            "contacts": [
              {
                "wa_id": "77071234567"
              }
            ],
            "metadata": {
              "display_phone_number": "77766170666",
              "phone_number_id": "123456789"
            }
          }
        }
      ]
    }
  ]
}
```

### Required raw fields

- `entry`
- `changes`
- `value`
- `messages`
- `from`
- `type`
- `id`

## 2. Normalized message

The first n8n step should normalize the webhook into a stable object.

```json
{
  "message_id": "wamid.xxx",
  "phone": "77071234567",
  "message_type": "text",
  "text": "Оператор",
  "media_id": null,
  "mime_type": null,
  "file_name": null,
  "received_at": "2026-06-24T10:00:00.000Z",
  "has_attachment": false,
  "raw_payload": {}
}
```

### Normalized fields

- `message_id`
- `phone`
- `message_type`
- `text`
- `media_id`
- `mime_type`
- `file_name`
- `received_at`
- `has_attachment`
- `raw_payload`

## 3. AI router output

The AI node must return strict JSON.

### Required fields

- `intent`
- `confidence`
- `extracted_fields`
- `reply`
- `required_action`
- `requires_manager`
- `next_state`

### Optional fields

- `support_topic`
- `reasoning_summary`

### Example

```json
{
  "intent": "human_operator",
  "confidence": 0.98,
  "extracted_fields": {},
  "reply": "Ваш запрос передан менеджеру. Ожидайте ответа.",
  "required_action": "handoff_to_manager",
  "requires_manager": true,
  "next_state": "manual",
  "support_topic": null,
  "reasoning_summary": "User explicitly requested human operator."
}
```

## 4. DB state fields

These fields must be loaded from and written to PostgreSQL.

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

### Recommended storage types

- `driver_id`: bigint or integer
- `phone`: text
- `state`: text
- `dialog_mode`: text
- `support_context`: jsonb
- `pending_correction_context`: jsonb
- `last_intent`: text
- `last_message_at`: timestamptz
- `application_status`: text
- `requires_attention`: boolean

## 5. WhatsApp reply payload

The reply node should send a WhatsApp Cloud API text message.

```json
{
  "messaging_product": "whatsapp",
  "to": "77071234567",
  "type": "text",
  "text": {
    "preview_url": false,
    "body": "Ваш запрос передан менеджеру. Ожидайте ответа."
  }
}
```

### Required reply fields

- `messaging_product`
- `to`
- `type`
- `text.preview_url`
- `text.body`

## 6. Database write contract

The migration layer should write at least:

- inbound message log
- outbound message log
- state update
- manual mode flag
- support context
- correction context

## 7. Attachment contract

For images and documents the normalized message must include:

- `message_type`
- `media_id`
- `mime_type`
- `file_name`
- `has_attachment`

### Attachment handling rule

- If `support_context` exists, the attachment is a screenshot/problem attachment.
- If `pending_correction_context` exists, the attachment is a correction document.
- If registration is active, the attachment is a registration document.

