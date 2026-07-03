"""Procrastinate task: finalize companion session — generate LLM title."""
from __future__ import annotations

import logging
import uuid as _uuid

from core.jobs.app import procrastinate_app
from core.jobs.tracker import mark_started, mark_completed, mark_failed, update_progress

logger = logging.getLogger("jobs.tasks.companion")


@procrastinate_app.task(name="finalize_companion_session")
async def finalize_companion_session(session_id: str, workspace: str, *, task_id: str) -> None:
    """Generate a title for the companion session via LLM and persist it."""
    tid = _uuid.UUID(task_id)
    await mark_started(tid)

    try:
        await update_progress(tid, 0.3, "Loading conversation")

        from sqlalchemy import select, update
        from core.db.engine import async_session
        from core.db.models import ChatSession, ChatMessage

        async with async_session() as session:
            result = await session.execute(
                select(ChatSession).where(ChatSession.id == _uuid.UUID(session_id))
            )
            db_session = result.scalar_one_or_none()
            if db_session is None:
                await mark_failed(tid, f"Session {session_id} not found")
                return

            msg_result = await session.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == _uuid.UUID(session_id))
                .order_by(ChatMessage.created_at)
                .limit(10)
            )
            messages = msg_result.scalars().all()

        await update_progress(tid, 0.7, "Generating title")

        new_title: str | None = None
        if len(messages) >= 6:
            try:
                from langchain_core.messages import HumanMessage
                from core.llm.provider import get_llm

                llm = get_llm(streaming=False)
                transcript = "\n".join(
                    f"{m.role}: {m.content}" for m in messages
                )
                prompt = (
                    "Summarize this companion conversation in 4-6 words for a title:\n"
                    f"{transcript}"
                )
                response = await llm.ainvoke([HumanMessage(content=prompt)])
                raw = response.content if hasattr(response, "content") else str(response)
                candidate = raw.strip().strip('"').strip("'")
                if candidate:
                    new_title = candidate
            except Exception as exc:
                logger.warning("Title generation failed for session %s: %s", session_id, exc)

        await update_progress(tid, 0.95, "Saving")

        if new_title:
            async with async_session() as session:
                await session.execute(
                    update(ChatSession)
                    .where(ChatSession.id == _uuid.UUID(session_id))
                    .values(title=new_title)
                )
                await session.commit()
            logger.info("finalize_companion_session: titled session %s → '%s'", session_id, new_title)

        await mark_completed(tid, result={"session_id": session_id, "title": new_title})

    except Exception as exc:
        logger.error("finalize_companion_session failed for %s: %s", session_id, exc)
        await mark_failed(tid, str(exc))
        raise
