"""SQLite schema versioning for chat history metadata.

Runs on every startup. Applies any pending migrations in order.
Safe to run multiple times (idempotent).
"""
from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger("core.memory.migrations")

SCHEMA_VERSION = 1

# Each migration is a function that receives an open sqlite3.Connection
MIGRATIONS: dict[int, callable] = {
    1: lambda conn: (
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                workspace TEXT NOT NULL,
                title TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                message_count INTEGER DEFAULT 0
            )
        """),
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sessions_workspace
            ON sessions(workspace)
        """),
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sessions_updated_at
            ON sessions(updated_at DESC)
        """),
    ),
}


def check_and_migrate(db_path: str) -> None:
    """Check schema version and run any pending migrations.

    Args:
        db_path: path to the SQLite database file
    """
    conn = sqlite3.connect(db_path)
    try:
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        if current < SCHEMA_VERSION:
            logger.info(f"Migrating DB {db_path}: version {current} → {SCHEMA_VERSION}")
            for version in range(current + 1, SCHEMA_VERSION + 1):
                if version in MIGRATIONS:
                    logger.debug(f"Applying migration {version}...")
                    MIGRATIONS[version](conn)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()
            logger.info(f"DB migration complete: {db_path}")
    finally:
        conn.close()
