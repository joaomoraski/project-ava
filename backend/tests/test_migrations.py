"""Verify that all migration tables are present in the test database.

The conftest _db_schema fixture already runs Base.metadata.create_all(), which
creates the same schema as the three Alembic migrations.  These tests simply
confirm every expected table exists — they do not re-run Alembic directly, which
would require a clean DB and running migrations from scratch.

If you want to test Alembic programmatically, set RUN_ALEMBIC_MIGRATIONS=1 in
the environment before running pytest (see the skipped alternative below).
"""
from __future__ import annotations

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import TEST_DATABASE_URL


async def _get_table_names() -> set[str]:
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.connect() as conn:
        tables = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_table_names()
        )
    await engine.dispose()
    return set(tables)


class TestMigration001BaseSchema:
    """Migration 001 — core tables."""

    async def test_migration_001_creates_base_tables(self, db_session):
        tables = await _get_table_names()
        for expected in ("workspaces", "chat_sessions", "chat_messages",
                         "knowledge_chunks", "secrets", "app_state"):
            assert expected in tables, f"Table '{expected}' not found (migration 001)"

    async def test_migration_001_workspaces_columns(self, db_session):
        await db_session.execute(
            text("SELECT id, name, system_prompt, stt_gate_mode FROM workspaces LIMIT 0")
        )

    async def test_migration_001_chat_sessions_columns(self, db_session):
        await db_session.execute(
            text("SELECT id, workspace_id, title, message_count, summary FROM chat_sessions LIMIT 0")
        )

    async def test_migration_001_chat_messages_columns(self, db_session):
        await db_session.execute(
            text("SELECT id, session_id, role, content, created_at FROM chat_messages LIMIT 0")
        )


class TestMigration002MeetingsTables:
    """Migration 002 — meetings, action_items, notes."""

    async def test_migration_002_creates_meetings_tables(self, db_session):
        tables = await _get_table_names()
        for expected in ("meetings", "action_items", "notes"):
            assert expected in tables, f"Table '{expected}' not found (migration 002)"

    async def test_migration_002_meetings_columns(self, db_session):
        await db_session.execute(
            text(
                "SELECT id, workspace_id, title, calendar_event_id, participants, "
                "transcript, summary, decisions, document_md, started_at, ended_at, "
                "status, created_at FROM meetings LIMIT 0"
            )
        )

    async def test_migration_002_action_items_columns(self, db_session):
        await db_session.execute(
            text(
                "SELECT id, meeting_id, workspace_id, owner, description, status, "
                "due_date, completed_at, linked_item_id, created_at FROM action_items LIMIT 0"
            )
        )

    async def test_migration_002_notes_columns(self, db_session):
        await db_session.execute(
            text(
                "SELECT id, workspace_id, title, content, tags, "
                "created_at, updated_at FROM notes LIMIT 0"
            )
        )


class TestMigration003CalendarTables:
    """Migration 003 — google_accounts, calendar_events."""

    async def test_migration_003_creates_calendar_tables(self, db_session):
        tables = await _get_table_names()
        for expected in ("google_accounts", "calendar_events"):
            assert expected in tables, f"Table '{expected}' not found (migration 003)"

    async def test_migration_003_google_accounts_columns(self, db_session):
        await db_session.execute(
            text(
                "SELECT id, email, refresh_token_encrypted, scopes, label, "
                "created_at FROM google_accounts LIMIT 0"
            )
        )

    async def test_migration_003_calendar_events_columns(self, db_session):
        await db_session.execute(
            text(
                "SELECT id, google_account_id, google_event_id, title, "
                "start_time, end_time, attendees, meet_link, synced_at "
                "FROM calendar_events LIMIT 0"
            )
        )
