# Meta webhook verification

## Goal

Meta must call:

`GET /webhook/whatsapp-verify?hub.mode=subscribe&hub.verify_token=sdfamily_meta_verify_2026&hub.challenge=123456`

and receive:

`HTTP 200`

body:

`123456`

content type:

`text/plain`

## Typical failures

- workflow inactive
- wrong webhook path
- test URL used instead of production URL
- verify token mismatch
- workflow returns JSON instead of plain text
- proxy or SSL issue in front of n8n

## Checklist

1. Verify webhook node is published.
2. Verify webhook node is on production URL.
3. Verify `META_VERIFY_TOKEN` matches Meta UI value.
4. Verify response body is raw `hub.challenge`.
5. Verify response header includes `Content-Type: text/plain`.
