# Test payloads for first n8n checks

Date: 2026-06-24

These payloads are meant for webhook testing against the `whatsapp_incoming_router` workflow.

## 1. WhatsApp text payload: "Здравствуйте"

```json
{
  "entry": [
    {
      "changes": [
        {
          "value": {
            "messages": [
              {
                "id": "wamid.test.hello",
                "from": "77071234567",
                "type": "text",
                "text": {
                  "body": "Здравствуйте"
                }
              }
            ],
            "contacts": [
              {
                "wa_id": "77071234567"
              }
            ]
          }
        }
      ]
    }
  ]
}
```

## 2. "Как вывести деньги?"

```json
{
  "entry": [
    {
      "changes": [
        {
          "value": {
            "messages": [
              {
                "id": "wamid.test.payout",
                "from": "77071234567",
                "type": "text",
                "text": {
                  "body": "Как вывести деньги?"
                }
              }
            ],
            "contacts": [
              {
                "wa_id": "77071234567"
              }
            ]
          }
        }
      ]
    }
  ]
}
```

## 3. "Оператор"

```json
{
  "entry": [
    {
      "changes": [
        {
          "value": {
            "messages": [
              {
                "id": "wamid.test.operator",
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
            ]
          }
        }
      ]
    }
  ]
}
```

## 4. "Нет вашего таксопарка в Яндекс Про"

```json
{
  "entry": [
    {
      "changes": [
        {
          "value": {
            "messages": [
              {
                "id": "wamid.test.yandex",
                "from": "77071234567",
                "type": "text",
                "text": {
                  "body": "Нет вашего таксопарка в Яндекс Про"
                }
              }
            ],
            "contacts": [
              {
                "wa_id": "77071234567"
              }
            ]
          }
        }
      ]
    }
  ]
}
```

## 5. "Осы такса паркке тіркелейін деп едім"

```json
{
  "entry": [
    {
      "changes": [
        {
          "value": {
            "messages": [
              {
                "id": "wamid.test.kz-reg",
                "from": "77071234567",
                "type": "text",
                "text": {
                  "body": "Осы такса паркке тіркелейін деп едім"
                }
              }
            ],
            "contacts": [
              {
                "wa_id": "77071234567"
              }
            ]
          }
        }
      ]
    }
  ]
}
```

## 6. Image/document payload example

### Image

```json
{
  "entry": [
    {
      "changes": [
        {
          "value": {
            "messages": [
              {
                "id": "wamid.test.image",
                "from": "77071234567",
                "type": "image",
                "image": {
                  "id": "media.image.1",
                  "mime_type": "image/jpeg",
                  "caption": "Скрин"
                }
              }
            ],
            "contacts": [
              {
                "wa_id": "77071234567"
              }
            ]
          }
        }
      ]
    }
  ]
}
```

### Document

```json
{
  "entry": [
    {
      "changes": [
        {
          "value": {
            "messages": [
              {
                "id": "wamid.test.doc",
                "from": "77071234567",
                "type": "document",
                "document": {
                  "id": "media.document.1",
                  "mime_type": "application/pdf",
                  "filename": "passport.pdf"
                }
              }
            ],
            "contacts": [
              {
                "wa_id": "77071234567"
              }
            ]
          }
        }
      ]
    }
  ]
}
```

