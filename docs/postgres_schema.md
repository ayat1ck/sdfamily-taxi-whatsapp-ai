# PostgreSQL schema for SD Family Taxi migration layer

Date: 2026-06-24

This schema is the minimal source-of-truth structure for n8n.

## 1. driver_states

```sql
CREATE TABLE IF NOT EXISTS driver_states (
    id BIGSERIAL PRIMARY KEY,
    driver_id BIGINT NULL,
    phone TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL DEFAULT 'new',
    dialog_mode TEXT NOT NULL DEFAULT 'auto',
    support_context JSONB NULL,
    pending_correction_context JSONB NULL,
    last_intent TEXT NULL,
    last_message_at TIMESTAMPTZ NULL,
    application_status TEXT NOT NULL DEFAULT 'collecting_data',
    requires_attention BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_driver_states_driver_id ON driver_states (driver_id);
CREATE INDEX IF NOT EXISTS idx_driver_states_state ON driver_states (state);
CREATE INDEX IF NOT EXISTS idx_driver_states_dialog_mode ON driver_states (dialog_mode);
```

## 2. conversation_messages

```sql
CREATE TABLE IF NOT EXISTS conversation_messages (
    id BIGSERIAL PRIMARY KEY,
    phone TEXT NOT NULL,
    driver_id BIGINT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('incoming', 'outgoing')),
    sender_type TEXT NOT NULL CHECK (sender_type IN ('customer', 'bot', 'manager', 'system')),
    message_type TEXT NOT NULL,
    text TEXT NULL,
    provider_message_id TEXT NULL,
    delivery_status TEXT NOT NULL DEFAULT 'pending',
    raw_payload JSONB NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversation_messages_phone ON conversation_messages (phone);
CREATE INDEX IF NOT EXISTS idx_conversation_messages_driver_id ON conversation_messages (driver_id);
CREATE INDEX IF NOT EXISTS idx_conversation_messages_created_at ON conversation_messages (created_at DESC);
```

## 3. manager_tickets

```sql
CREATE TABLE IF NOT EXISTS manager_tickets (
    id BIGSERIAL PRIMARY KEY,
    phone TEXT NOT NULL,
    driver_id BIGINT NULL,
    topic TEXT NOT NULL,
    reason TEXT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    priority INTEGER NOT NULL DEFAULT 1,
    source_message_id BIGINT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS idx_manager_tickets_phone ON manager_tickets (phone);
CREATE INDEX IF NOT EXISTS idx_manager_tickets_status ON manager_tickets (status);
CREATE INDEX IF NOT EXISTS idx_manager_tickets_topic ON manager_tickets (topic);
```

## 4. Minimal notes

- `driver_states.phone` is the main lookup key for n8n.
- `conversation_messages` stores both inbound and outbound history.
- `manager_tickets` stores durable human handoff items.
- `support_context` and `pending_correction_context` should be JSONB so n8n can update them safely.

## 5. Optional trigger pattern

You may optionally keep `updated_at` fresh with an application-side update or a DB trigger.

