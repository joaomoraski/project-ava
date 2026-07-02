"""Add google_accounts and calendar_events tables.

Revision ID: 003
Revises: 002
Create Date: 2026-04-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- google_accounts ---
    op.create_table(
        "google_accounts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(255), unique=True, nullable=False),
        sa.Column("refresh_token_encrypted", sa.LargeBinary, nullable=False),
        sa.Column("scopes", JSONB, server_default="[]"),
        sa.Column("label", sa.String(100), server_default="''"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # --- calendar_events ---
    op.create_table(
        "calendar_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("google_account_id", UUID(as_uuid=True), sa.ForeignKey("google_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("google_event_id", sa.String(255), nullable=False),
        sa.Column("title", sa.String(500), server_default="''"),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attendees", JSONB, server_default="[]"),
        sa.Column("meet_link", sa.String(500), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_calendar_events_google_account_id", "calendar_events", ["google_account_id"])
    op.create_index("ix_calendar_events_start_time", "calendar_events", ["start_time"])


def downgrade() -> None:
    op.drop_index("ix_calendar_events_start_time", table_name="calendar_events")
    op.drop_index("ix_calendar_events_google_account_id", table_name="calendar_events")
    op.drop_table("calendar_events")
    op.drop_table("google_accounts")
