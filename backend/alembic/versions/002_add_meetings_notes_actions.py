"""Add meetings, notes, and action_items tables.

Revision ID: 002
Revises: 001
Create Date: 2026-04-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- meetings ---
    op.create_table(
        "meetings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(255), server_default="''"),
        sa.Column("calendar_event_id", sa.String(255), nullable=True),
        sa.Column("participants", JSONB, server_default="[]"),
        sa.Column("transcript", sa.Text, server_default="''"),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("decisions", JSONB, server_default="[]"),
        sa.Column("document_md", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), server_default="'recording'"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_meetings_workspace_id", "meetings", ["workspace_id"])
    op.create_index("ix_meetings_status", "meetings", ["status"])

    # --- action_items ---
    op.create_table(
        "action_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("meeting_id", UUID(as_uuid=True), sa.ForeignKey("meetings.id", ondelete="CASCADE"), nullable=True),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner", sa.String(255), nullable=True),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("status", sa.String(20), server_default="'pending'"),
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("linked_item_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_action_items_workspace_id", "action_items", ["workspace_id"])
    op.create_index("ix_action_items_status", "action_items", ["status"])
    op.create_index("ix_action_items_meeting_id", "action_items", ["meeting_id"])

    # --- notes ---
    op.create_table(
        "notes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("content", sa.Text, server_default="''"),
        sa.Column("tags", JSONB, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_notes_workspace_id", "notes", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_notes_workspace_id", table_name="notes")
    op.drop_table("notes")

    op.drop_index("ix_action_items_meeting_id", table_name="action_items")
    op.drop_index("ix_action_items_status", table_name="action_items")
    op.drop_index("ix_action_items_workspace_id", table_name="action_items")
    op.drop_table("action_items")

    op.drop_index("ix_meetings_status", table_name="meetings")
    op.drop_index("ix_meetings_workspace_id", table_name="meetings")
    op.drop_table("meetings")
