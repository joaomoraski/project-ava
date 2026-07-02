"""Pytest fixtures for the Ava Project backend tests.

Strategy
--------
* Schema creation: done once per session via asyncio.run() (sync fixture) so it
  never fights with test-level event loops.
* db_session: each async test gets its own engine + session on its own loop —
  avoids the "Future attached to a different loop" asyncpg bug.
* client: FastAPI TestClient (sync); DATABASE_URL env-var is pointed at the
  test DB so the app creates its own engine on Starlette's loop.

IMPORTANT — test DB safety
--------------------------
Tests call drop_all() before and after every session. To prevent accidental
wipes of the dev/production database, this file refuses to run if
TEST_DATABASE_URL does not point at a DB whose name ends with '_test'.

Always run tests via:  make test
Or set the env var:    export TEST_DATABASE_URL=postgresql+asyncpg://ava:ava_dev_password@localhost:5432/ava_test

IMPORT ORDER IS CRITICAL
------------------------
os.environ["DATABASE_URL"] MUST be set before any project module is imported.
core.db.engine creates a module-level engine = create_async_engine(settings.database_url)
at import time. If it is imported before DATABASE_URL is overridden, it permanently
binds to the dev DB for the entire test session. The block below sets the env var
FIRST, then enforces the safety guard, then allows project imports to proceed.
"""
from __future__ import annotations

# ── Step 1: resolve test URL and override DATABASE_URL BEFORE any project import ──
import os

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://ava:ava_dev_password@localhost:5432/ava_test",
)

# Force pydantic-settings / module-level engine to use the test DB.
# This must happen before ANY "from core.*" or "from main import app" call.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL


# ── Step 2: safety guard — abort if URL still looks like dev/prod ─────────────

def _abort_if_test_url_unsafe(test_url: str) -> None:
    """Refuse to run if the test DB URL looks like the dev/prod DB.

    drop_all() is irreversible. Past incident: tests run with TEST_DATABASE_URL
    unset wiped the dev DB. We compare against the default dev URL AND enforce
    that the DB name ends with '_test'.
    """
    # Default dev URL baked into Settings (do NOT import settings here —
    # that would create the singleton before DATABASE_URL env var is set).
    DEV_URL = "postgresql+asyncpg://ava:ava_dev_password@localhost:5432/ava"

    test_norm = test_url.strip()

    # Reject if it literally matches the known dev URL.
    if test_norm == DEV_URL:
        raise SystemExit(
            "\nERROR: REFUSING TO RUN TESTS — TEST_DATABASE_URL matches the dev DATABASE_URL.\n"
            "  This would call drop_all() on your real database.\n"
            "  Set TEST_DATABASE_URL to a separate DB (default: ava_test).\n"
        )

    # Enforce that the DB name ends with '_test' regardless of host/user.
    db_name = test_norm.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    if not db_name.endswith("_test"):
        raise SystemExit(
            f"\nERROR: REFUSING TO RUN TESTS — TEST_DATABASE_URL database name "
            f"'{db_name}' does not end with '_test'.\n"
            f"  This is a safety check after a past data-loss incident.\n"
            f"  Use postgresql+asyncpg://...:5432/ava_test (or any name ending in _test).\n"
        )


_abort_if_test_url_unsafe(TEST_DATABASE_URL)

# ── Step 3: all other imports (project modules may now be imported safely) ────
import asyncio
import json

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


# ── Auto-create ava_test DB if it doesn't exist yet ──────────────────────────

async def _ensure_test_db_exists() -> None:
    """Connect to the 'postgres' admin DB and CREATE the test DB if absent."""
    admin_url = TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres"
    db_name = TEST_DATABASE_URL.rsplit("/", 1)[1].split("?")[0]
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT", echo=False)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": db_name}
            )
            if result.scalar() is None:
                await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    finally:
        await engine.dispose()


# ── One-time schema setup (sync, avoids cross-loop issues) ───────────────────

@pytest.fixture(scope="session", autouse=True)
def _db_schema():
    """Create tables once before the session; drop after."""
    from core.db.models import Base

    async def _create():
        await _ensure_test_db_exists()
        engine = create_async_engine(TEST_DATABASE_URL, echo=False)
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    async def _drop():
        engine = create_async_engine(TEST_DATABASE_URL, echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            # Also drop alembic_version so the next server start re-runs migrations
            await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        await engine.dispose()

    asyncio.run(_create())
    yield
    asyncio.run(_drop())


# ── Per-test async DB session (fresh engine per test — no cross-loop issues) ──

@pytest_asyncio.fixture()
async def db_session(_db_schema):
    """Async DB session for a single test; rolled back after."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()
    await engine.dispose()


# ── Temp filesystem (autouse — needed by secrets/plugins/STT tests) ──────────

@pytest.fixture(scope="session", autouse=True)
def test_workspace_dirs(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ava_test")
    orig_dir = os.getcwd()
    os.chdir(tmp)

    for d in [
        "workspaces/test/chat_history",
        "knowledge/meetings",
        "knowledge/documents",
        "secrets",
        "logs",
        "plugins",
        "mcp",
    ]:
        os.makedirs(d, exist_ok=True)

    with open("mcp/servers.json", "w") as f:
        json.dump({"servers": {}}, f)
    with open("plugins/installed.json", "w") as f:
        json.dump({"plugins": {}}, f)
    with open("workspaces/test/config.json", "w") as f:
        json.dump({
            "name": "test",
            "system_prompt": "",
            "stt_gate_mode": "smart",
            "proactivity": "medium",
            "tools_enabled": [],
            "plugins_enabled": [],
            "collections": [],
            "transcription_priority": {},
            "knowledge_seeds": [],
        }, f)

    yield tmp
    os.chdir(orig_dir)


# ── FastAPI TestClient ────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def client(_db_schema, test_workspace_dirs):
    """Sync TestClient; app uses the test DB — DATABASE_URL was set at conftest module load."""
    import unittest.mock as mock

    # DATABASE_URL is already set to TEST_DATABASE_URL at the top of this file,
    # before any project module was imported. No monkeypatching needed here.

    async def _fake_init():
        from core.db.engine import async_session
        from core.workspace import workspace_exists, create_workspace
        async with async_session() as session:
            if not await workspace_exists(session, "personal"):
                await create_workspace(session, "personal")

    with mock.patch("main._init_database", side_effect=_fake_init):
        with mock.patch("main.run_startup_checks", return_value=None):
            from main import app
            from fastapi.testclient import TestClient
            with TestClient(app, raise_server_exceptions=True) as c:
                yield c


@pytest.fixture(scope="session")
def monkeypatch_session():
    """Session-scoped monkeypatch (pytest's built-in is function-scoped)."""
    import unittest.mock as mock
    patches = []

    class _MP:
        def setenv(self, key, value):
            p = mock.patch.dict(os.environ, {key: value})
            p.start()
            patches.append(p)

    mp = _MP()
    yield mp
    for p in patches:
        p.stop()
