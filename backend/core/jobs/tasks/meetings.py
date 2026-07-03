"""Procrastinate task: generate and embed meeting document."""
from __future__ import annotations

import logging
import uuid

from procrastinate import RetryStrategy

from core.jobs.app import procrastinate_app
from core.jobs.tracker import mark_started, mark_completed, mark_failed, update_progress

logger = logging.getLogger("jobs.tasks.meetings")


@procrastinate_app.task(
    name="generate_meeting_doc",
    retry=RetryStrategy(max_attempts=3, exponential_wait=10),
)
async def generate_meeting_doc(meeting_id: str, workspace: str, *, task_id: str) -> None:
    """Generate meeting document then embed it into the knowledge base."""
    tid = uuid.UUID(task_id)
    await mark_started(tid)

    try:
        from core.db.engine import async_session
        from core.knowledge.meeting_doc import generate_meeting_document, embed_meeting_document

        await update_progress(tid, 0.1, "Loading transcript")

        async with async_session() as session:
            await update_progress(tid, 0.4, "Generating summary via LLM")
            await generate_meeting_document(meeting_id, session)

        await update_progress(tid, 0.7, "Embedding chunks")

        async with async_session() as session:
            await embed_meeting_document(meeting_id, workspace, session)

        await update_progress(tid, 0.95, "Finalizing")
        await mark_completed(tid, result={"meeting_id": meeting_id, "workspace": workspace})
        logger.info("generate_meeting_doc completed for meeting %s", meeting_id)

    except Exception as exc:
        logger.error("generate_meeting_doc failed for meeting %s: %s", meeting_id, exc)
        await mark_failed(tid, str(exc))
        raise  # allow procrastinate to retry
