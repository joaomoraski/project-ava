"""Procrastinate App singleton.

The DSN is derived from DATABASE_URL by stripping the +asyncpg driver suffix
because procrastinate uses psycopg (psycopg3) directly.
"""
from __future__ import annotations

import os

from procrastinate import App, PsycopgConnector

_raw_url = os.environ.get("DATABASE_URL", "postgresql://ava:ava_dev_password@localhost:5432/ava")
_DSN = _raw_url.replace("postgresql+asyncpg://", "postgresql://")

procrastinate_app = App(
    connector=PsycopgConnector(conninfo=_DSN),
    import_paths=["core.jobs.tasks"],
)

# Import task modules eagerly so procrastinate_app discovers all tasks at import time.
try:
    import core.jobs.tasks  # noqa: F401
except Exception:
    pass
