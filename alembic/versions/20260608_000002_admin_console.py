"""admin console schema"""

from alembic import op
import sqlalchemy as sa


revision = "20260608_000002"
down_revision = "20260603_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("drivers", sa.Column("assigned_manager_name", sa.String(length=255), nullable=True))
    op.add_column("drivers", sa.Column("dialog_mode", sa.String(length=32), nullable=False, server_default="bot_active"))
    op.add_column("drivers", sa.Column("unread_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("drivers", sa.Column("requires_attention", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("drivers", sa.Column("duplicate_flag", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("drivers", sa.Column("admin_notes", sa.String(length=2048), nullable=True))
    op.add_column("drivers", sa.Column("admin_tags", sa.String(length=512), nullable=True))
    op.add_column("drivers", sa.Column("deletion_requested_at", sa.DateTime(), nullable=True))
    op.add_column("drivers", sa.Column("paused_at", sa.DateTime(), nullable=True))
    op.add_column("drivers", sa.Column("closed_at", sa.DateTime(), nullable=True))

    op.add_column("messages", sa.Column("sender_type", sa.String(length=32), nullable=False, server_default="customer"))
    op.add_column("messages", sa.Column("provider_message_id", sa.String(length=255), nullable=True))
    op.add_column("messages", sa.Column("media_url", sa.String(length=512), nullable=True))
    op.add_column("messages", sa.Column("mime_type", sa.String(length=128), nullable=True))
    op.add_column("messages", sa.Column("delivery_status", sa.String(length=64), nullable=True))
    op.add_column("messages", sa.Column("error_text", sa.String(length=1024), nullable=True))
    op.add_column("messages", sa.Column("is_read_by_admin", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("messages", sa.Column("read_at", sa.DateTime(), nullable=True))

    with op.batch_alter_table("documents") as batch_op:
        batch_op.add_column(sa.Column("message_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("file_name", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("mime_type", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("storage_provider", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("storage_path", sa.String(length=512), nullable=True))
        batch_op.create_foreign_key("fk_documents_message_id", "messages", ["message_id"], ["id"])
        batch_op.create_index("ix_documents_message_id", ["message_id"], unique=False)

    op.create_table(
        "admin_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=120), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_admin_accounts_username", "admin_accounts", ["username"], unique=True)

    op.create_table(
        "conversation_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("driver_id", sa.Integer(), sa.ForeignKey("drivers.id"), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("event_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_conversation_events_driver_id", "conversation_events", ["driver_id"], unique=False)
    op.create_index("ix_conversation_events_event_type", "conversation_events", ["event_type"], unique=False)

    op.create_table(
        "application_audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("applications.id"), nullable=True),
        sa.Column("driver_id", sa.Integer(), sa.ForeignKey("drivers.id"), nullable=False),
        sa.Column("actor_type", sa.String(length=64), nullable=False, server_default="shared_admin"),
        sa.Column("field_name", sa.String(length=128), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("action_type", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_application_audit_logs_application_id", "application_audit_logs", ["application_id"], unique=False)
    op.create_index("ix_application_audit_logs_driver_id", "application_audit_logs", ["driver_id"], unique=False)

    op.create_table(
        "integration_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("applications.id"), nullable=True),
        sa.Column("driver_id", sa.Integer(), sa.ForeignKey("drivers.id"), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=True),
        sa.Column("response_payload", sa.JSON(), nullable=True),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_integration_jobs_application_id", "integration_jobs", ["application_id"], unique=False)
    op.create_index("ix_integration_jobs_driver_id", "integration_jobs", ["driver_id"], unique=False)
    op.create_index("ix_integration_jobs_provider", "integration_jobs", ["provider"], unique=False)
    op.create_index("ix_integration_jobs_status", "integration_jobs", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_integration_jobs_status", table_name="integration_jobs")
    op.drop_index("ix_integration_jobs_provider", table_name="integration_jobs")
    op.drop_index("ix_integration_jobs_driver_id", table_name="integration_jobs")
    op.drop_index("ix_integration_jobs_application_id", table_name="integration_jobs")
    op.drop_table("integration_jobs")

    op.drop_index("ix_application_audit_logs_driver_id", table_name="application_audit_logs")
    op.drop_index("ix_application_audit_logs_application_id", table_name="application_audit_logs")
    op.drop_table("application_audit_logs")

    op.drop_index("ix_conversation_events_event_type", table_name="conversation_events")
    op.drop_index("ix_conversation_events_driver_id", table_name="conversation_events")
    op.drop_table("conversation_events")

    op.drop_index("ix_admin_accounts_username", table_name="admin_accounts")
    op.drop_table("admin_accounts")

    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_index("ix_documents_message_id")
        batch_op.drop_constraint("fk_documents_message_id", type_="foreignkey")
        batch_op.drop_column("storage_path")
        batch_op.drop_column("storage_provider")
        batch_op.drop_column("mime_type")
        batch_op.drop_column("file_name")
        batch_op.drop_column("message_id")

    op.drop_column("messages", "read_at")
    op.drop_column("messages", "is_read_by_admin")
    op.drop_column("messages", "error_text")
    op.drop_column("messages", "delivery_status")
    op.drop_column("messages", "mime_type")
    op.drop_column("messages", "media_url")
    op.drop_column("messages", "provider_message_id")
    op.drop_column("messages", "sender_type")

    op.drop_column("drivers", "closed_at")
    op.drop_column("drivers", "paused_at")
    op.drop_column("drivers", "deletion_requested_at")
    op.drop_column("drivers", "admin_tags")
    op.drop_column("drivers", "admin_notes")
    op.drop_column("drivers", "duplicate_flag")
    op.drop_column("drivers", "requires_attention")
    op.drop_column("drivers", "unread_count")
    op.drop_column("drivers", "dialog_mode")
    op.drop_column("drivers", "assigned_manager_name")
