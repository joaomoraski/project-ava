"""AI tool: load all items linked to a named context."""
from __future__ import annotations

from langchain_core.tools import tool
from sqlalchemy import select, func

from core.db.engine import async_session
from core.db.models import Context, ContextLink, Meeting, Note, Todo, ActionItem, Workspace


@tool
async def get_context(name: str, workspace: str) -> str:
    """Load all items linked to a named context (e.g. 'Tabby', 'Project X').

    Returns full content of every linked meeting, note, todo, and action item,
    formatted as a single readable document. Use this when the user references
    a project/theme by name, especially phrases like 'the <name> context',
    'about <name>', 'what's happening with <name>'.
    """
    async with async_session() as session:
        # Resolve workspace to id
        ws_result = await session.execute(
            select(Workspace.id).where(Workspace.name == workspace)
        )
        workspace_id = ws_result.scalar_one_or_none()
        if workspace_id is None:
            return f"Workspace '{workspace}' not found."

        # Case-insensitive context lookup
        stmt = select(Context).where(
            func.lower(Context.name) == name.lower(),
            Context.workspace_id == workspace_id,
        )
        ctx = (await session.execute(stmt)).scalar_one_or_none()
        if ctx is None:
            return f"No context named '{name}' found in workspace '{workspace}'."

        # Fetch all links
        links = (
            await session.execute(
                select(ContextLink).where(ContextLink.context_id == ctx.id)
            )
        ).scalars().all()

        meetings: list[Meeting] = []
        notes: list[Note] = []
        todos: list[Todo] = []
        action_items: list[ActionItem] = []

        for link in links:
            uid = link.item_id
            if link.item_type == "meeting":
                obj = (await session.execute(select(Meeting).where(Meeting.id == uid))).scalar_one_or_none()
                if obj:
                    meetings.append(obj)
            elif link.item_type == "note":
                obj = (await session.execute(select(Note).where(Note.id == uid))).scalar_one_or_none()
                if obj:
                    notes.append(obj)
            elif link.item_type == "todo":
                obj = (await session.execute(select(Todo).where(Todo.id == uid))).scalar_one_or_none()
                if obj:
                    todos.append(obj)
            elif link.item_type == "action_item":
                obj = (await session.execute(select(ActionItem).where(ActionItem.id == uid))).scalar_one_or_none()
                if obj:
                    action_items.append(obj)

    # Format as markdown document
    parts: list[str] = []
    parts.append(f"# Context: {ctx.name}")
    if ctx.description:
        parts.append(ctx.description)
    parts.append("")

    # Meetings
    parts.append(f"## Meetings ({len(meetings)})")
    for m in meetings:
        date_str = m.started_at.strftime("%Y-%m-%d") if m.started_at else "unknown date"
        parts.append(f"### {m.title or 'Untitled'} — {date_str}")
        if m.participants:
            parts.append(f"Participants: {', '.join(str(p) for p in m.participants)}")
        if m.summary:
            parts.append(f"Summary: {m.summary}")
        if m.transcript:
            excerpt = m.transcript[:1500]
            if len(m.transcript) > 1500:
                excerpt += "... [truncated]"
            parts.append(f"Transcript excerpt:\n{excerpt}")
        parts.append("")

    # Notes
    parts.append(f"## Notes ({len(notes)})")
    for n in notes:
        parts.append(f"### {n.title}")
        if n.tags:
            parts.append(f"Tags: {', '.join(str(t) for t in n.tags)}")
        parts.append(n.content or "")
        parts.append("")

    # Todos
    open_todos = [t for t in todos if t.status != "done"]
    done_todos = [t for t in todos if t.status == "done"]
    parts.append(f"## Todos ({len(open_todos)} open, {len(done_todos)} done)")
    for t in todos:
        check = "x" if t.status == "done" else " "
        meta_parts: list[str] = []
        if t.priority:
            meta_parts.append(t.priority)
        if t.due_date:
            meta_parts.append(f"due {t.due_date.strftime('%Y-%m-%d')}")
        meta = f" ({', '.join(meta_parts)})" if meta_parts else ""
        parts.append(f"- [{check}] {t.title}{meta}")
        if t.description:
            parts.append(f"  {t.description}")
    parts.append("")

    # Action items
    parts.append(f"## Action items ({len(action_items)})")
    for a in action_items:
        meta_parts = []
        if a.owner:
            meta_parts.append(f"owner: {a.owner}")
        if a.due_date:
            meta_parts.append(f"due {a.due_date.strftime('%Y-%m-%d')}")
        meta_parts.append(f"status: {a.status}")
        meta = f" ({', '.join(meta_parts)})"
        parts.append(f"- {a.description}{meta}")
    parts.append("")

    return "\n".join(parts)
