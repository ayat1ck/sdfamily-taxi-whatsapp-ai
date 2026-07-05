# First live WhatsApp message test

## Goal

Make one real WhatsApp message travel through n8n, into PostgreSQL, through AI, and back to WhatsApp.

## Test message

`Здравствуйте`

## Expected result

- incoming message logged in `conversation_messages`
- row exists in `driver_states`
- AI Router returns strict JSON
- outgoing reply is sent
- reply is UTF-8

## If it fails

- check execution log in n8n
- check AI Router node output format
- check WhatsApp send node response
- check PostgreSQL credential
- check Meta webhook subscription path
