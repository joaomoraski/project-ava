"""Add todos and alerts tables.

Revision ID: 004
Revises: 003
Create Date: 2026-04-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- todos ---
    op.create_table(
        "todos",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text, server_default="''"),
        sa.Column("priority", sa.String(20), server_default="'medium'"),
        sa.Column("status", sa.String(20), server_default="'pending'"),
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_todos_workspace", "todos", ["workspace_id"])
    op.create_index("idx_todos_status", "todos", ["status"])
    op.create_index(
        "idx_todos_due_date", "todos", ["due_date"],
        postgresql_where=sa.text("due_date IS NOT NULL"),
    )

    # --- alerts ---
    op.create_table(
        "alerts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("message", sa.Text, server_default="''"),
        sa.Column("trigger_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("repeat_rule", sa.String(50), server_default="'once'"),
        sa.Column("status", sa.String(20), server_default="'pending'"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_alerts_workspace", "alerts", ["workspace_id"])
    op.create_index(
        "idx_alerts_trigger", "alerts", ["trigger_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index("idx_alerts_status", "alerts", ["status"])


def downgrade() -> None:
    op.drop_index("idx_alerts_status", table_name="alerts")
    op.drop_index("idx_alerts_trigger", table_name="alerts")
    op.drop_index("idx_alerts_workspace", table_name="alerts")
    op.drop_table("alerts")

    op.drop_index("idx_todos_due_date", table_name="todos")
    op.drop_index("idx_todos_status", table_name="todos")
    op.drop_index("idx_todos_workspace", table_name="todos")
    op.drop_table("todos")
