"""Add job_tasks table and procrastinate schema.

Revision ID: 009
Revises: 008
Create Date: 2026-04-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- job_tasks table ---
    op.create_table(
        "job_tasks",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("job_type", sa.String, nullable=False),
        sa.Column("target_type", sa.String, nullable=True),
        sa.Column("target_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "workspace_id",
            UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String, nullable=False, server_default="queued"),
        sa.Column("progress", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("progress_message", sa.String, nullable=True),
        sa.Column("procrastinate_job_id", sa.BigInteger, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("result_payload", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_job_tasks_job_type", "job_tasks", ["job_type"])
    op.create_index("ix_job_tasks_target_id", "job_tasks", ["target_id"])
    op.create_index("ix_job_tasks_workspace_id", "job_tasks", ["workspace_id"])
    op.create_index("ix_job_tasks_status", "job_tasks", ["status"])
    op.create_index(
        "ix_job_tasks_workspace_status_created",
        "job_tasks",
        ["workspace_id", "status", "created_at"],
    )

    # --- procrastinate schema ---
    # Apply procrastinate's canonical schema SQL via a dedicated sync psycopg
    # connection. The alembic-bound connection here is asyncpg-based, which
    # does not support multi-statement SQL or the standard cursor context
    # manager protocol. Opening a separate connection keeps this simple.
    import os
    from importlib import resources as importlib_resources
    import psycopg

    schema_sql = (
        importlib_resources.files("procrastinate.sql")
        .joinpath("schema.sql")
        .read_text(encoding="utf-8")
    )
    raw_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://ava:ava_dev_password@localhost:5432/ava",
    )
    sync_dsn = (
        raw_url.replace("postgresql+asyncpg://", "postgresql://")
        .replace("postgresql+psycopg://", "postgresql://")
    )
    with psycopg.connect(sync_dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(schema_sql)


def downgrade() -> None:
    # Drop procrastinate tables (canonical list from procrastinate docs)
    for tbl in [
        "procrastinate_periodic_defers",
        "procrastinate_events",
        "procrastinate_jobs",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE")

    # Drop job_tasks
    op.drop_index("ix_job_tasks_workspace_status_created", table_name="job_tasks")
    op.drop_index("ix_job_tasks_status", table_name="job_tasks")
    op.drop_index("ix_job_tasks_workspace_id", table_name="job_tasks")
    op.drop_index("ix_job_tasks_target_id", table_name="job_tasks")
    op.drop_index("ix_job_tasks_job_type", table_name="job_tasks")
    op.drop_table("job_tasks")
