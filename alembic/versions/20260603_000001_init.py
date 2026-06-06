"""initial schema"""

from alembic import op
import sqlalchemy as sa


revision = "20260603_000001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "drivers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("whatsapp_phone", sa.String(length=32), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("last_name", sa.String(length=120), nullable=True),
        sa.Column("first_name", sa.String(length=120), nullable=True),
        sa.Column("middle_name", sa.String(length=120), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=True),
        sa.Column("address", sa.String(length=255), nullable=True),
        sa.Column("iin", sa.String(length=12), nullable=True),
        sa.Column("birth_date", sa.String(length=32), nullable=True),
        sa.Column("driving_experience_since", sa.String(length=32), nullable=True),
        sa.Column("driver_license_number", sa.String(length=64), nullable=True),
        sa.Column("driver_license_issue_date", sa.String(length=32), nullable=True),
        sa.Column("driver_license_expires_at", sa.String(length=32), nullable=True),
        sa.Column("executor_type", sa.String(length=64), nullable=True),
        sa.Column("employment_type", sa.String(length=64), nullable=True),
        sa.Column("hired_at", sa.String(length=32), nullable=True),
        sa.Column("has_personal_car", sa.String(length=8), nullable=True),
        sa.Column("existing_vehicle_lookup", sa.String(length=120), nullable=True),
        sa.Column("is_hearing_impaired", sa.String(length=8), nullable=True),
        sa.Column("state", sa.String(length=64), nullable=False, server_default="new"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_message_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_drivers_whatsapp_phone", "drivers", ["whatsapp_phone"], unique=True)

    op.create_table(
        "vehicles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("driver_id", sa.Integer(), sa.ForeignKey("drivers.id"), nullable=False),
        sa.Column("brand", sa.String(length=120), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("year", sa.String(length=8), nullable=True),
        sa.Column("plate_number", sa.String(length=32), nullable=True),
        sa.Column("color", sa.String(length=64), nullable=True),
        sa.Column("vin", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_vehicles_driver_id", "vehicles", ["driver_id"], unique=True)

    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("driver_id", sa.Integer(), sa.ForeignKey("drivers.id"), nullable=False),
        sa.Column("document_type", sa.String(length=64), nullable=False),
        sa.Column("file_url", sa.String(length=512), nullable=True),
        sa.Column("google_drive_file_id", sa.String(length=255), nullable=True),
        sa.Column("whatsapp_media_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="uploaded"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_documents_driver_id", "documents", ["driver_id"], unique=False)

    op.create_table(
        "applications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("driver_id", sa.Integer(), sa.ForeignKey("drivers.id"), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False, server_default="collecting_data"),
        sa.Column("yandex_status", sa.String(length=64), nullable=True),
        sa.Column("yandex_driver_id", sa.String(length=128), nullable=True),
        sa.Column("yandex_vehicle_id", sa.String(length=128), nullable=True),
        sa.Column("yandex_error", sa.String(length=1024), nullable=True),
        sa.Column("sent_to_yandex_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_applications_driver_id", "applications", ["driver_id"], unique=True)

    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("driver_id", sa.Integer(), sa.ForeignKey("drivers.id"), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("message_type", sa.String(length=32), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_messages_driver_id", "messages", ["driver_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_messages_driver_id", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_applications_driver_id", table_name="applications")
    op.drop_table("applications")
    op.drop_index("ix_documents_driver_id", table_name="documents")
    op.drop_table("documents")
    op.drop_index("ix_vehicles_driver_id", table_name="vehicles")
    op.drop_table("vehicles")
    op.drop_index("ix_drivers_whatsapp_phone", table_name="drivers")
    op.drop_table("drivers")
