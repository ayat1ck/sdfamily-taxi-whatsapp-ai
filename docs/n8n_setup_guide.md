# n8n setup guide for SD Family Taxi

## Environment variables

Set these in the n8n runtime, not in the workflow JSON:

- `AI_PROVIDER=gemini`
- `AI_ROUTER_MODEL=gemini-2.5-flash`
- `GEMINI_API_KEY`
- `AI_ROUTER_PROMPT`
- `AI_ROUTER_SCHEMA_JSON`
- `WHATSAPP_ACCESS_TOKEN`
- `WHATSAPP_PHONE_NUMBER_ID`
- `META_VERIFY_TOKEN=sdfamily_meta_verify_2026`
- `WHATSAPP_API_BASE_URL=https://graph.facebook.com/v20.0`
- `OPENAI_API_KEY` only if you keep OpenAI as fallback

Gemini endpoint:

`https://generativelanguage.googleapis.com/v1beta/models/{{$env.AI_ROUTER_MODEL || 'gemini-2.5-flash'}}:generateContent?key={{$env.GEMINI_API_KEY}}`

## Credentials

Create one PostgreSQL credential in n8n:

- name: `Postgres SD Family Taxi`
- host: Render Postgres host
- port: `5432`
- database: `taxi_ai_manager`
- user: `taxi_ai_manager`
- password: Render DB password
- SSL: `Require`

Do not store Gemini or WhatsApp secrets inside workflow JSON.

## Import order

1. Import the main WhatsApp workflow.
2. Import the manager escalation workflow if you keep it as a subworkflow.
3. Save credentials.
4. Publish subworkflows first.
5. Publish the main workflow second.

## Meta Webhooks

Use one callback URL in Meta:

- `https://n8n.bljw.org/webhook/whatsapp-incoming-router`

Use this verify token:

- `sdfamily_meta_verify_2026`

Meta should receive a plain text `hub.challenge` on GET verification.

## Manual verification test

Run:

`curl "https://n8n.bljw.org/webhook/whatsapp-verify?hub.mode=subscribe&hub.verify_token=sdfamily_meta_verify_2026&hub.challenge=123456"`

Expected output:

`123456`

## First live message

Send one WhatsApp message from a test phone:

- `Здравствуйте`

Expected:

- execution appears in n8n
- AI Router returns JSON
- reply is sent back in UTF-8
- no mojibake

## What to check in execution

- verify endpoint returns plain text challenge
- `Normalize Message` extracts `phone`, `text`, `message_type`
- `driver_states` is loaded or created
- Gemini response is parsed from `response.candidates[0].content.parts[0].text`
- `conversation_messages` gets an incoming row before AI
- outgoing WhatsApp reply is sent
