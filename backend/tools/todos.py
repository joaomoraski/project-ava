"""LangChain tool for managing user todos."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from langchain_core.tools import tool

logger = logging.getLogger("tools.todos")


@tool
async def manage_todos(
    action: str,
    title: str = "",
    description: str = "",
    priority: str = "medium",
    status: str = "",
    todo_id: str = "",
    due_date: str = "",
    workspace: str = "personal",
) -> str:
    """Manage the user's todo list. Actions: create, list, complete, delete, update.
    Use this when the user asks to add tasks, check todos, mark something done, etc.

    Args:
        action: one of create / list / complete / delete / update
        title: todo title (required for create)
        description: optional description
        priority: low / medium / high / urgent (default: medium)
        status: pending or done (for update/complete)
        todo_id: UUID of the todo (required for complete, delete, update)
        due_date: ISO 8601 datetime string (optional)
        workspace: target workspace (default: personal)
    """
    try:
        from sqlalchemy import select

        from core.db.engine import async_session
        from core.db.models import Todo, Workspace

        async with async_session() as session:
            # Resolve workspace
            ws_result = await session.execute(
                select(Workspace.id).where(Workspace.name == workspace)
            )
            ws_id = ws_result.scalar_one_or_none()
            if ws_id is None:
                return f"Workspace '{workspace}' not found."

            # ── create ──────────────────────────────────────────────────────
            if action == "create":
                if not title:
                    return "Please provide a title for the todo."
                due = None
                if due_date:
                    try:
                        due = datetime.fromisoformat(due_date)
                        if due.tzinfo is None:
                            due = due.replace(tzinfo=timezone.utc)
                    except ValueError:
                        return f"Invalid due_date format: '{due_date}'. Use ISO 8601."

                todo = Todo(
                    workspace_id=ws_id,
                    title=title,
                    description=description,
                    priority=priority,
                    due_date=due,
                )
                session.add(todo)
                await session.commit()
                await session.refresh(todo)
                return f"Created todo '{todo.title}' (id={todo.id}, priority={priority})."

            # ── list ─────────────────────────────────────────────────────────
            elif action == "list":
                stmt = select(Todo).where(Todo.workspace_id == ws_id)
                if status:
                    stmt = stmt.where(Todo.status == status)
                stmt = stmt.order_by(Todo.created_at.desc()).limit(20)
                result = await session.execute(stmt)
                todos = result.scalars().all()

                if not todos:
                    return "No todos found."

                lines = []
                for t in todos:
                    due_str = f" | due: {t.due_date.strftime('%Y-%m-%d')}" if t.due_date else ""
                    lines.append(
                        f"- [{t.status.upper()}] [{t.priority}] {t.title}{due_str} (id={t.id})"
                    )
                return "\n".join(lines)

            # ── complete ──────────────────────────────────────────────────────
            elif action == "complete":
                if not todo_id:
                    return "Please provide a todo_id to mark as complete."
                import uuid as _uuid
                try:
                    uid = _uuid.UUID(todo_id)
                except ValueError:
                    return f"Invalid todo_id: '{todo_id}'."

                result = await session.execute(select(Todo).where(Todo.id == uid))
                todo = result.scalar_one_or_none()
                if todo is None:
                    return f"Todo '{todo_id}' not found."

                todo.status = "done"
                todo.completed_at = datetime.now(timezone.utc)
                await session.commit()
                return f"Marked todo '{todo.title}' as done."

            # ── delete ────────────────────────────────────────────────────────
            elif action == "delete":
                if not todo_id:
                    return "Please provide a todo_id to delete."
                import uuid as _uuid
                try:
                    uid = _uuid.UUID(todo_id)
                except ValueError:
                    return f"Invalid todo_id: '{todo_id}'."

                result = await session.execute(select(Todo).where(Todo.id == uid))
                todo = result.scalar_one_or_none()
                if todo is None:
                    return f"Todo '{todo_id}' not found."

                title_str = todo.title
                await session.delete(todo)
                await session.commit()
                return f"Deleted todo '{title_str}'."

            # ── update ────────────────────────────────────────────────────────
            elif action == "update":
                if not todo_id:
                    return "Please provide a todo_id to update."
                import uuid as _uuid
                try:
                    uid = _uuid.UUID(todo_id)
                except ValueError:
                    return f"Invalid todo_id: '{todo_id}'."

                result = await session.execute(select(Todo).where(Todo.id == uid))
                todo = result.scalar_one_or_none()
                if todo is None:
                    return f"Todo '{todo_id}' not found."

                if title:
                    todo.title = title
                if description:
                    todo.description = description
                if priority:
                    todo.priority = priority
                if status:
                    todo.status = status
                    if status == "done" and todo.completed_at is None:
                        todo.completed_at = datetime.now(timezone.utc)
                    elif status == "pending":
                        todo.completed_at = None
                if due_date:
                    try:
                        due = datetime.fromisoformat(due_date)
                        if due.tzinfo is None:
                            due = due.replace(tzinfo=timezone.utc)
                        todo.due_date = due
                    except ValueError:
                        return f"Invalid due_date: '{due_date}'."

                await session.commit()
                await session.refresh(todo)
                return f"Updated todo '{todo.title}' (id={todo.id})."

            else:
                return f"Unknown action '{action}'. Use: create, list, complete, delete, update."

    except Exception as e:
        logger.error(f"manage_todos failed: {e}")
        return f"Todo operation failed: {e}"
