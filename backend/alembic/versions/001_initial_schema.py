"""Initial schema — all tables, pgvector, pg_trgm.

Revision ID: 001
Revises: None
Create Date: 2026-03-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB
from pgvector.sqlalchemy import Vector

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Extensions (idempotent)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # --- workspaces ---
    op.create_table(
        "workspaces",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(64), unique=True, nullable=False, index=True),
        sa.Column("system_prompt", sa.Text, server_default=""),
        sa.Column("stt_gate_mode", sa.String(20), server_default="smart"),
        sa.Column("proactivity", sa.String(20), server_default="medium"),
        sa.Column("tools_enabled", JSONB, server_default="[]"),
        sa.Column("plugins_enabled", JSONB, server_default="[]"),
        sa.Column("collections", JSONB, server_default="[]"),
        sa.Column("transcription_priority", JSONB, server_default="{}"),
        sa.Column("knowledge_seeds", JSONB, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # --- chat_sessions ---
    op.create_table(
        "chat_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("message_count", sa.Integer, server_default="0"),
        sa.Column("summary", sa.Text, nullable=True),
    )
    op.create_index("ix_sessions_workspace_updated", "chat_sessions", ["workspace_id", "updated_at"])

    # --- chat_messages ---
    op.create_table(
        "chat_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("session_id", UUID(as_uuid=True), sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_messages_session_created", "chat_messages", ["session_id", "created_at"])
    op.execute(
        "CREATE INDEX ix_messages_content_fts ON chat_messages USING gin (content gin_trgm_ops)"
    )

    # --- knowledge_chunks ---
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("collection", sa.String(64), server_default="'documents'"),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("embedding", Vector(768)),
        sa.Column("source", sa.String(1024), nullable=False),
        sa.Column("source_type", sa.String(20), server_default="'file'"),
        sa.Column("file_name", sa.String(255), server_default="''"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_chunks_workspace_source", "knowledge_chunks", ["workspace_id", "source"])
    op.create_index("ix_chunks_workspace_collection", "knowledge_chunks", ["workspace_id", "collection"])
    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw ON knowledge_chunks "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )

    # --- secrets ---
    op.create_table(
        "secrets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(128), unique=True, nullable=False, index=True),
        sa.Column("encrypted_value", sa.LargeBinary, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # --- app_state ---
    op.create_table(
        "app_state",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("app_state")
    op.drop_table("secrets")
    op.drop_table("knowledge_chunks")
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")
    op.drop_table("workspaces")
