# dialog_v2

`dialog_v2` is the new rewrite path for the WhatsApp bot dialog core. It runs alongside the legacy `DialogueEngine`.

Enable with:

```env
USE_DIALOG_V2=true
USE_DIALOG_V2_PHONE_ALLOWLIST=
```

- Empty `USE_DIALOG_V2_PHONE_ALLOWLIST` => dialog v2 for **all** senders.
- Comma-separated phones (without `+`) => limited rollout, e.g. `77001112233,77002223344`.

## Flow map

- `registration`
  - document-first onboarding
  - document upload handling
  - draft merge and summary generation
- `existing_driver`
  - detects already-connected drivers
  - shows the support menu
  - routes menu actions to manager or profile update
- `profile_update`
  - collects a change request ticket
  - keeps the payload in `support_context_json`
- `support`
  - sends money/tariff/blocking/Yandex issues to manager
  - falls back to FAQ for simple questions
- `faq`
  - lightweight canned answers for common questions
- `manager`
  - creates the handoff payload
  - stores manager alert data for admins

## Router priorities

1. Media messages go to `registration`.
2. Active manager handoff state goes to `manager`.
3. Existing driver phrases go to `existing_driver`.
4. Profile update phrases go to `profile_update`.
5. Support escalation phrases go to `support`.
6. FAQ phrases go to `faq`.
7. Everything else falls back to `registration`.

## What AI does

- intent hints and routing helpers
- OCR and document type resolution hooks
- FAQ stub expansion later

## What backend does

- keeps all dialog state in `support_context_json`
- stores drafts, tickets, and manager alerts
- emits conversation events
- persists incoming messages and documents

## Event bus

Current flow-level events:

- `existing_driver_found`
- `profile_update_requested`
- `support_ticket_created`
- `manager_handoff`

## Current tests

- registration document-first entry
- OCR-based document type resolution
- final summary rendering
- existing driver menu routing
- profile update menu routing
- manager handoff payloads
- FAQ and support routing

## Notes

- The legacy `app/dialog/engine.py` is intentionally untouched.
- Yandex submission is not implemented yet in `dialog_v2`.
- OCR extraction is still hook-based and intentionally shallow for the MVP stage.
