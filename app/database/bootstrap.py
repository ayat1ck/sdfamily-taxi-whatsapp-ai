from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.admin.models import AdminAccount
from app.applications.models import Application
from app.audit.models import ApplicationAuditLog
from app.conversation_events.models import ConversationEvent
from app.documents.models import Document
from app.drivers.models import Driver
from app.integration_jobs.models import IntegrationJob
from app.messages.models import Message
from app.vehicles.models import Vehicle


TABLE_COLUMN_DEFS: dict[str, dict[str, str]] = {
    "drivers": {
        "assigned_manager_name": "VARCHAR(255)",
        "dialog_mode": "VARCHAR(32) DEFAULT 'bot_active' NOT NULL",
        "unread_count": "INTEGER DEFAULT 0 NOT NULL",
        "requires_attention": "BOOLEAN DEFAULT FALSE NOT NULL",
        "duplicate_flag": "BOOLEAN DEFAULT FALSE NOT NULL",
        "admin_notes": "VARCHAR(2048)",
        "admin_tags": "VARCHAR(512)",
        "deletion_requested_at": "{datetime_type}",
        "paused_at": "{datetime_type}",
        "closed_at": "{datetime_type}",
    },
    "messages": {
        "sender_type": "VARCHAR(32) DEFAULT 'customer' NOT NULL",
        "provider_message_id": "VARCHAR(255)",
        "media_url": "VARCHAR(512)",
        "mime_type": "VARCHAR(128)",
        "delivery_status": "VARCHAR(64)",
        "error_text": "VARCHAR(1024)",
        "is_read_by_admin": "BOOLEAN DEFAULT FALSE NOT NULL",
        "read_at": "{datetime_type}",
    },
    "documents": {
        "message_id": "INTEGER",
        "file_name": "VARCHAR(255)",
        "mime_type": "VARCHAR(128)",
        "storage_provider": "VARCHAR(64)",
        "storage_path": "VARCHAR(512)",
    },
}


def ensure_runtime_schema(engine: Engine) -> None:
    with engine.begin() as conn:
        datetime_type = "TIMESTAMP" if engine.dialect.name == "postgresql" else "DATETIME"
        for table in [
            Driver.__table__,
            Vehicle.__table__,
            Document.__table__,
            Application.__table__,
            Message.__table__,
            AdminAccount.__table__,
            ConversationEvent.__table__,
            ApplicationAuditLog.__table__,
            IntegrationJob.__table__,
        ]:
            table.create(bind=conn, checkfirst=True)

        inspector = inspect(conn)
        for table_name, column_defs in TABLE_COLUMN_DEFS.items():
            existing = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, column_def in column_defs.items():
                if column_name in existing:
                    continue
                resolved_def = column_def.format(datetime_type=datetime_type)
                conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {resolved_def}"))
