"""Add contexts and context_links tables.

Revision ID: 007
Revises: 004
Create Date: 2026-04-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "007"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- contexts ---
    op.create_table(
        "contexts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text, server_default="''"),
        sa.Column("color", sa.String(7), server_default="'#6366f1'"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_contexts_workspace", "contexts", ["workspace_id"])

    # --- context_links ---
    op.create_table(
        "context_links",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("context_id", UUID(as_uuid=True), sa.ForeignKey("contexts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_type", sa.String(32), nullable=False),
        sa.Column("item_id", UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_context_links_context", "context_links", ["context_id"])
    op.create_index("ix_context_links_item", "context_links", ["item_type", "item_id"])


def downgrade() -> None:
    op.drop_index("ix_context_links_item", table_name="context_links")
    op.drop_index("ix_context_links_context", table_name="context_links")
    op.drop_table("context_links")

    op.drop_index("ix_contexts_workspace", table_name="contexts")
    op.drop_table("contexts")
