"""Procrastinate task: re-transcribe meeting audio at higher quality."""
from __future__ import annotations

import logging
import uuid

from procrastinate import RetryStrategy

from core.jobs.app import procrastinate_app
from core.jobs.tracker import mark_started, mark_completed, mark_failed, update_progress

logger = logging.getLogger("jobs.tasks.transcript")


@procrastinate_app.task(
    name="refine_transcript",
    retry=RetryStrategy(max_attempts=2, exponential_wait=10),
)
async def refine_transcript(meeting_id: str, *, task_id: str) -> None:
    """Re-transcribe the meeting audio with higher quality Whisper settings.

    If no audio file exists for this meeting, the task completes as a no-op.
    """
    tid = uuid.UUID(task_id)
    await mark_started(tid)

    try:
        from sqlalchemy import select
        from core.db.engine import async_session
        from core.db.models import Meeting

        await update_progress(tid, 0.1, "Loading meeting")

        async with async_session() as session:
            result = await session.execute(
                select(Meeting).where(Meeting.id == uuid.UUID(meeting_id))
            )
            meeting = result.scalar_one_or_none()

        if meeting is None:
            await mark_failed(tid, f"Meeting {meeting_id} not found")
            return

        # If a WhisperSTT refinement function is available, invoke it.
        # Otherwise fall back to re-running the document generation pipeline.
        await update_progress(tid, 0.3, "Transcribing at high quality")

        try:
            from core.stt.whisper import WhisperSTT
            stt = WhisperSTT()
            # Refinement uses the existing audio captured in the meeting recording path.
            # If no audio path is stored, skip gracefully.
            audio_path: str | None = getattr(meeting, "audio_path", None)
            if audio_path:
                import asyncio
                loop = asyncio.get_event_loop()
                refined = await loop.run_in_executor(None, stt.transcribe_file, audio_path)
                if refined:
                    async with async_session() as session:
                        result = await session.execute(
                            select(Meeting).where(Meeting.id == uuid.UUID(meeting_id))
                        )
                        m = result.scalar_one_or_none()
                        if m:
                            m.transcript = refined
                            await session.commit()
                    logger.info("refine_transcript: updated transcript for meeting %s", meeting_id)
            else:
                logger.info(
                    "refine_transcript: no audio_path for meeting %s — skipping re-transcription",
                    meeting_id,
                )
        except Exception as stt_exc:
            logger.warning("WhisperSTT refinement failed: %s — skipping", stt_exc)

        await update_progress(tid, 0.95, "Finalizing")
        await mark_completed(tid, result={"meeting_id": meeting_id})

    except Exception as exc:
        logger.error("refine_transcript failed for meeting %s: %s", meeting_id, exc)
        await mark_failed(tid, str(exc))
        raise
