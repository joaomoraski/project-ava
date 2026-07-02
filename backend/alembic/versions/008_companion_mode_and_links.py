"""Add companion mode, session_date, auto_linked, and meeting_links table.

Revision ID: 008
Revises: 007
Create Date: 2026-04-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- chat_sessions: add mode and session_date ---
    op.add_column(
        "chat_sessions",
        sa.Column("mode", sa.String(16), nullable=False, server_default="chat"),
    )
    op.add_column(
        "chat_sessions",
        sa.Column("session_date", sa.Date, nullable=True),
    )
    op.create_index(
        "ix_chat_sessions_mode_date",
        "chat_sessions",
        ["workspace_id", "mode", "session_date"],
    )

    # --- context_links: add auto_linked ---
    op.add_column(
        "context_links",
        sa.Column("auto_linked", sa.Boolean, nullable=False, server_default=sa.text("false")),
    )

    # --- meeting_links table ---
    op.create_table(
        "meeting_links",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "meeting_id",
            UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "related_meeting_id",
            UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("link_type", sa.String(32), nullable=False, server_default="'related'"),
        sa.Column("source", sa.String(16), nullable=False, server_default="'user'"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("meeting_id", "related_meeting_id", name="uq_meeting_links_pair"),
    )
    op.create_index("ix_meeting_links_meeting_id", "meeting_links", ["meeting_id"])
    op.create_index("ix_meeting_links_related_id", "meeting_links", ["related_meeting_id"])


def downgrade() -> None:
    op.drop_index("ix_meeting_links_related_id", table_name="meeting_links")
    op.drop_index("ix_meeting_links_meeting_id", table_name="meeting_links")
    op.drop_table("meeting_links")

    op.drop_column("context_links", "auto_linked")

    op.drop_index("ix_chat_sessions_mode_date", table_name="chat_sessions")
    op.drop_column("chat_sessions", "session_date")
    op.drop_column("chat_sessions", "mode")
