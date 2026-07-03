"""Procrastinate task: auto-categorize an item into matching contexts using LLM."""
from __future__ import annotations

import json
import logging
import uuid as _uuid

from core.jobs.app import procrastinate_app
from core.jobs.tracker import mark_started, mark_completed, mark_failed, update_progress

logger = logging.getLogger("jobs.tasks.contexts")

_VALID_TYPES = {"meeting", "note", "todo", "action_item"}


@procrastinate_app.task(name="auto_categorize_item")
async def auto_categorize_item(
    target_type: str,
    target_id: str,
    workspace_id: str,
    *,
    task_id: str | None = None,
) -> None:
    """Auto-link an item to relevant contexts using LLM classification.

    Strict workspace guardrails:
    1. workspace_id must be non-empty — abort immediately if missing.
    2. The loaded item's workspace_id must match the provided workspace_id.
    3. Context queries are always scoped to workspace_id.

    task_id is optional. When provided, progress is tracked via the job tracker.
    When None, the categorization runs untracked (fire-and-forget).
    """
    tid = _uuid.UUID(task_id) if task_id else None

    # ── Guardrail 1: workspace_id must be present ──────────────────────────────
    if not workspace_id or not workspace_id.strip():
        logger.error("auto_categorize aborted: missing workspace_id (target=%s/%s)", target_type, target_id)
        if tid is not None:
            await mark_failed(tid, "auto_categorize aborted: missing workspace_id")
        return

    try:
        ws_uuid = _uuid.UUID(workspace_id)
    except ValueError:
        logger.error("auto_categorize aborted: invalid workspace_id '%s'", workspace_id)
        if tid is not None:
            await mark_failed(tid, f"invalid workspace_id: {workspace_id}")
        return

    if target_type not in _VALID_TYPES:
        logger.error("auto_categorize aborted: unsupported target_type '%s'", target_type)
        if tid is not None:
            await mark_failed(tid, f"unsupported target_type: {target_type}")
        return

    if tid is not None:
        await mark_started(tid)

    try:
        from sqlalchemy import select, and_
        from core.db.engine import async_session
        from core.db.models import (
            Context,
            ContextLink,
            Meeting,
            Note,
            Todo,
            ActionItem,
        )

        if tid is not None:
            await update_progress(tid, 0.1, "Loading item")

        # ── Load the item and verify workspace ownership ──────────────────────
        async with async_session() as session:
            item_title, item_content, item_ws_id = await _load_item(
                session, target_type, target_id
            )

        if item_title is None:
            if tid is not None:
                await mark_failed(tid, f"{target_type} {target_id} not found")
            return

        # ── Guardrail 2: workspace mismatch ───────────────────────────────────
        if item_ws_id != ws_uuid:
            logger.error(
                "auto_categorize aborted: workspace mismatch for %s %s "
                "(item.workspace_id=%s, provided=%s)",
                target_type, target_id, item_ws_id, ws_uuid,
            )
            if tid is not None:
                await mark_failed(tid, "workspace mismatch — item does not belong to the given workspace")
            return

        if tid is not None:
            await update_progress(tid, 0.25, "Loading contexts")

        # ── Load contexts scoped strictly to this workspace ───────────────────
        async with async_session() as session:
            ctx_result = await session.execute(
                select(Context).where(Context.workspace_id == ws_uuid)
            )
            contexts = ctx_result.scalars().all()

        if not contexts:
            logger.info(
                "auto_categorize: no contexts in workspace %s — skipping item %s/%s",
                ws_uuid, target_type, target_id,
            )
            if tid is not None:
                await mark_completed(tid, result={"linked": []})
            return

        if tid is not None:
            await update_progress(tid, 0.45, "Classifying with LLM")

        # ── Build LLM prompt ──────────────────────────────────────────────────
        ctx_list = [(c.name, c.description or "") for c in contexts]
        context_lines = "\n".join(f'- "{n}": {d}' for n, d in ctx_list)
        content_snippet = (item_content or "")[:1000]

        prompt = (
            f"Given this item:\n"
            f"  title: {item_title!r}\n"
            f"  content: {content_snippet!r}\n\n"
            f"And these contexts:\n{context_lines}\n\n"
            f"Return ONLY a JSON array of context NAMES this item belongs to. "
            f"Empty array if none. Example: [\"Work\", \"Finances\"] or []\n"
            f"Output format: JSON array only, no explanation."
        )

        matched_names: list[str] = []
        try:
            from langchain_core.messages import HumanMessage
            from core.llm.provider import get_llm

            llm = get_llm(streaming=False)
            response = await llm.ainvoke([HumanMessage(content=prompt)])
            raw = response.content if hasattr(response, "content") else str(response)

            # Parse JSON — find the first [...] in the response
            import re
            m = re.search(r"\[.*?\]", raw, re.DOTALL)
            if m:
                matched_names = json.loads(m.group(0))
                if not isinstance(matched_names, list):
                    matched_names = []
        except Exception as exc:
            logger.warning("LLM classification failed for %s/%s: %s", target_type, target_id, exc)
            matched_names = []

        if tid is not None:
            await update_progress(tid, 0.75, "Creating context links")

        # ── Create ContextLink rows (skip duplicates) ─────────────────────────
        linked: list[str] = []
        name_to_ctx = {c.name: c for c in contexts}
        item_uuid = _uuid.UUID(target_id)

        for name in matched_names:
            ctx = name_to_ctx.get(name)
            if ctx is None:
                # Case-insensitive fallback
                ctx = next(
                    (c for c in contexts if c.name.lower() == name.lower()), None
                )
            if ctx is None:
                logger.debug("auto_categorize: context '%s' not found — skipping", name)
                continue

            async with async_session() as session:
                existing = await session.execute(
                    select(ContextLink).where(
                        and_(
                            ContextLink.context_id == ctx.id,
                            ContextLink.item_type == target_type,
                            ContextLink.item_id == item_uuid,
                        )
                    )
                )
                if existing.scalar_one_or_none() is not None:
                    logger.debug(
                        "auto_categorize: link already exists for context '%s' + %s/%s",
                        name, target_type, target_id,
                    )
                    linked.append(name)
                    continue

                link = ContextLink(
                    context_id=ctx.id,
                    item_type=target_type,
                    item_id=item_uuid,
                    auto_linked=True,
                )
                session.add(link)
                await session.commit()
                linked.append(name)
                logger.info(
                    "auto_categorize: linked %s/%s to context '%s'",
                    target_type, target_id, name,
                )

        if tid is not None:
            await update_progress(tid, 0.95, "Done")
            await mark_completed(tid, result={"linked": linked})
        logger.info(
            "auto_categorize done: %s/%s → %s", target_type, target_id, linked
        )

    except Exception as exc:
        logger.error("auto_categorize_item failed: %s", exc)
        if tid is not None:
            await mark_failed(tid, str(exc))
        raise


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _load_item(
    session,
    target_type: str,
    target_id: str,
) -> tuple[str | None, str | None, _uuid.UUID | None]:
    """Return (title, content, workspace_id) for the item, or (None, None, None)."""
    from sqlalchemy import select
    from core.db.models import Meeting, Note, Todo, ActionItem

    uid = _uuid.UUID(target_id)

    if target_type == "meeting":
        result = await session.execute(select(Meeting).where(Meeting.id == uid))
        obj = result.scalar_one_or_none()
        if obj is None:
            return None, None, None
        return (
            obj.title or f"Meeting {target_id}",
            obj.transcript or obj.summary or "",
            obj.workspace_id,
        )

    if target_type == "note":
        result = await session.execute(select(Note).where(Note.id == uid))
        obj = result.scalar_one_or_none()
        if obj is None:
            return None, None, None
        return obj.title, obj.content or "", obj.workspace_id

    if target_type == "todo":
        result = await session.execute(select(Todo).where(Todo.id == uid))
        obj = result.scalar_one_or_none()
        if obj is None:
            return None, None, None
        return obj.title, obj.description or "", obj.workspace_id

    if target_type == "action_item":
        result = await session.execute(select(ActionItem).where(ActionItem.id == uid))
        obj = result.scalar_one_or_none()
        if obj is None:
            return None, None, None
        return obj.description, obj.description or "", obj.workspace_id

    return None, None, None
