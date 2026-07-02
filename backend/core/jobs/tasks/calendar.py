"""Procrastinate task: sync Google Calendar for all connected accounts."""
from __future__ import annotations

import logging
import uuid as _uuid

from core.jobs.app import procrastinate_app
from core.jobs.tracker import mark_started, mark_completed, mark_failed, update_progress

logger = logging.getLogger("jobs.tasks.calendar")


@procrastinate_app.task(name="sync_google_calendar")
async def sync_google_calendar(workspace: str, *, task_id: str) -> None:
    """Sync Google Calendar events for a workspace.

    Called from the main.py loop every 5 minutes (replaces the asyncio loop with
    an enqueue call so the work runs in the procrastinate worker).
    """
    tid = _uuid.UUID(task_id)
    await mark_started(tid)

    try:
        await update_progress(tid, 0.2, "Connecting to Google Calendar")

        from api.calendar import _do_sync

        await _do_sync()

        await update_progress(tid, 0.95, "Sync complete")
        await mark_completed(tid, result={"workspace": workspace})
        logger.info("sync_google_calendar done for workspace %s", workspace)

    except Exception as exc:
        logger.warning("sync_google_calendar failed (non-fatal): %s", exc)
        # Don't raise — calendar sync failure should not block the worker queue.
        await mark_failed(tid, str(exc))
