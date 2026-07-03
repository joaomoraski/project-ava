"""Tests for the auto_categorize_item procrastinate task."""
from __future__ import annotations

import inspect
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.jobs.tasks.contexts import auto_categorize_item


class TestAutoCategorizSignature:
    """task_id must be optional so callers can defer without it."""

    def test_task_id_has_default_none(self):
        # Unwrap the underlying coroutine function from the procrastinate task wrapper
        fn = auto_categorize_item.func  # type: ignore[attr-defined]
        sig = inspect.signature(fn)
        param = sig.parameters.get("task_id")
        assert param is not None, "task_id parameter not found"
        assert param.default is None, (
            f"task_id should default to None, got {param.default!r}"
        )

    def test_task_id_is_keyword_only(self):
        fn = auto_categorize_item.func  # type: ignore[attr-defined]
        sig = inspect.signature(fn)
        param = sig.parameters["task_id"]
        assert param.kind == inspect.Parameter.KEYWORD_ONLY


class TestAutoCategorizWithoutTaskId:
    """Calling the underlying coroutine without task_id must not crash on tracker calls."""

    @pytest.mark.asyncio
    async def test_aborts_on_missing_workspace_id_no_task_id(self):
        """When workspace_id is blank and task_id is None, should log + return (no tracker crash)."""
        fn = auto_categorize_item.func  # type: ignore[attr-defined]
        # Should complete without raising TypeError from mark_failed(None, ...)
        await fn(
            target_type="todo",
            target_id=str(uuid.uuid4()),
            workspace_id="",
            task_id=None,
        )

    @pytest.mark.asyncio
    async def test_aborts_on_invalid_target_type_no_task_id(self):
        """Unsupported target_type with task_id=None returns cleanly."""
        fn = auto_categorize_item.func  # type: ignore[attr-defined]
        ws_id = str(uuid.uuid4())
        await fn(
            target_type="invalid_type",
            target_id=str(uuid.uuid4()),
            workspace_id=ws_id,
            task_id=None,
        )

    @pytest.mark.asyncio
    async def test_item_not_found_no_task_id(self):
        """If item doesn't exist in DB, task should log and return — no tracker crash."""
        fn = auto_categorize_item.func  # type: ignore[attr-defined]
        ws_id = str(uuid.uuid4())

        with patch(
            "core.jobs.tasks.contexts._load_item",
            new=AsyncMock(return_value=(None, None, None)),
        ):
            # Should not raise TypeError about task_id
            await fn(
                target_type="todo",
                target_id=str(uuid.uuid4()),
                workspace_id=ws_id,
                task_id=None,
            )

    @pytest.mark.asyncio
    async def test_with_task_id_calls_tracker(self):
        """Passing task_id as a string UUID should call mark_started + mark_failed when item is missing."""
        fn = auto_categorize_item.func  # type: ignore[attr-defined]
        ws_id = str(uuid.uuid4())
        tid = str(uuid.uuid4())
        item_id = str(uuid.uuid4())

        # Provide a fake async_session context manager so the real asyncpg pool is never touched
        mock_session = AsyncMock()
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("core.db.engine.async_session", return_value=mock_ctx), \
             patch(
                 "core.jobs.tasks.contexts._load_item",
                 new=AsyncMock(return_value=(None, None, None)),
             ), \
             patch("core.jobs.tasks.contexts.mark_started", new_callable=AsyncMock) as ms, \
             patch("core.jobs.tasks.contexts.mark_failed", new_callable=AsyncMock) as mf, \
             patch("core.jobs.tasks.contexts.mark_completed", new_callable=AsyncMock), \
             patch("core.jobs.tasks.contexts.update_progress", new_callable=AsyncMock):
            await fn(
                target_type="todo",
                target_id=item_id,
                workspace_id=ws_id,
                task_id=tid,
            )
            # mark_started must have been called (workspace valid, type valid) then
            # mark_failed because item was not found
            ms.assert_called_once()
            mf.assert_called_once()
