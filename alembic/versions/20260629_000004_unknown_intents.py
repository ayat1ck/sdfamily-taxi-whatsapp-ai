"""add unknown intents table

Revision ID: 20260629_000004
Revises: 20260610_000003
Create Date: 2026-06-29 23:30:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260629_000004"
down_revision = "20260610_000003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "unknown_intents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("driver_id", sa.Integer(), sa.ForeignKey("drivers.id"), nullable=True),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("messages.id"), nullable=True),
        sa.Column("state_before", sa.String(length=64), nullable=True),
        sa.Column("message_text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=True),
        sa.Column("message_type", sa.String(length=32), nullable=True),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_unknown_intents_driver_id", "unknown_intents", ["driver_id"])
    op.create_index("ix_unknown_intents_message_id", "unknown_intents", ["message_id"])


def downgrade() -> None:
    op.drop_index("ix_unknown_intents_message_id", table_name="unknown_intents")
    op.drop_index("ix_unknown_intents_driver_id", table_name="unknown_intents")
    op.drop_table("unknown_intents")
