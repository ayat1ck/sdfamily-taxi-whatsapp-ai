# Secrets checklist

## Do not store in workflow JSON

- `GEMINI_API_KEY`
- `OPENAI_API_KEY`
- `WHATSAPP_ACCESS_TOKEN`
- `WHATSAPP_PHONE_NUMBER_ID`
- `META_VERIFY_TOKEN`
- PostgreSQL password
- any bearer token

## Put in n8n runtime env

- `AI_PROVIDER`
- `AI_ROUTER_MODEL`
- `GEMINI_API_KEY`
- `OPENAI_API_KEY` if used
- `AI_ROUTER_PROMPT`
- `AI_ROUTER_SCHEMA_JSON`
- `WHATSAPP_ACCESS_TOKEN`
- `WHATSAPP_PHONE_NUMBER_ID`
- `META_VERIFY_TOKEN`
- `WHATSAPP_API_BASE_URL`

## Put in n8n credential store

- PostgreSQL connection

## Before commit

- remove secrets from exported JSON
- replace raw tokens with env references
- verify no private keys are present in docs or workflow files
- rotate any secret that was pasted into chat
