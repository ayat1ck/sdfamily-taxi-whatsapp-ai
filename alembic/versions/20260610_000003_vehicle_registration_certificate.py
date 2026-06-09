"""add vehicle registration_certificate

Revision ID: 20260610_000003
Revises: 20260608_000002
Create Date: 2026-06-10
"""

from alembic import op
import sqlalchemy as sa


revision = "20260610_000003"
down_revision = "20260608_000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vehicles", sa.Column("registration_certificate", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("vehicles", "registration_certificate")
