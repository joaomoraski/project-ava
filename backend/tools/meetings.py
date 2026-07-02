"""Meeting-related tools for LLM agent."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from langchain_core.tools import tool

logger = logging.getLogger("tools.meetings")


@tool
async def search_meetings(query: str, workspace: str) -> str:
    """Search past meeting transcripts and summaries using semantic search. Use this when the user asks about past meetings, discussions, or what was said.

    Args:
        query: what to search for
        workspace: which workspace to search in (required)
    """
    try:
        import uuid as _uuid

        from sqlalchemy import select

        from core.db.engine import async_session
        from core.db.models import Meeting, Workspace
        from core.knowledge.rag import KnowledgeBase

        kb = KnowledgeBase(workspace=workspace)

        async with async_session() as session:
            # Over-fetch so meeting chunks aren't crowded out by non-meeting results
            rag_results = await kb.search(session, query, top_k=20)
            meeting_results = [r for r in rag_results if r.get("source_type") == "meeting"][:5]

            # Parse meeting UUIDs from source field (format: "meeting:<uuid>")
            meeting_ids: list[_uuid.UUID] = []
            for r in meeting_results:
                source = r.get("source", "")
                if source.startswith("meeting:"):
                    try:
                        meeting_ids.append(_uuid.UUID(source.split(":", 1)[1]))
                    except ValueError:
                        pass

            meetings: list = []
            if meeting_ids:
                result = await session.execute(
                    select(Meeting)
                    .where(Meeting.id.in_(meeting_ids))
                    .order_by(Meeting.started_at.desc())
                )
                meetings = result.scalars().all()

            # Fallback: SQL ilike restricted to workspace if RAG returned nothing
            if not meetings:
                ws_result = await session.execute(
                    select(Workspace.id).where(Workspace.name == workspace)
                )
                ws_id = ws_result.scalar_one_or_none()
                if ws_id is None:
                    return f"Workspace '{workspace}' not found."
                result = await session.execute(
                    select(Meeting)
                    .where(Meeting.workspace_id == ws_id)
                    .where(
                        Meeting.title.ilike(f"%{query}%")
                        | Meeting.transcript.ilike(f"%{query}%")
                        | Meeting.summary.ilike(f"%{query}%")
                    )
                    .order_by(Meeting.started_at.desc())
                    .limit(5)
                )
                meetings = result.scalars().all()

        if not meetings:
            return f"No meetings found matching: {query}"

        lines = []
        for m in meetings:
            date_str = m.started_at.strftime("%Y-%m-%d %H:%M") if m.started_at else "unknown date"
            snippet = ""
            transcript = m.transcript or ""
            idx = transcript.lower().find(query.lower())
            if idx != -1:
                start = max(0, idx - 100)
                end = min(len(transcript), idx + 100)
                snippet = transcript[start:end].strip()
            elif m.summary:
                snippet = m.summary[:200]
            lines.append(
                f"**{m.title or 'Untitled'}** ({date_str}) [status: {m.status}]\n"
                f"Snippet: ...{snippet}..."
            )

        return "\n\n---\n\n".join(lines)
    except Exception as e:
        logger.error(f"search_meetings failed: {e}")
        return f"Meeting search failed: {e}"


@tool
async def list_recent_meetings(workspace: str, days: int = 7) -> str:
    """List recent meetings within a time window.

    Args:
        workspace: which workspace (required)
        days: how many days back to look (default: 7)
    """
    try:
        from datetime import timedelta, timezone

        from sqlalchemy import select

        from core.db.engine import async_session
        from core.db.models import Meeting, Workspace

        since = datetime.now(timezone.utc) - timedelta(days=days)

        async with async_session() as session:
            ws_result = await session.execute(
                select(Workspace.id).where(Workspace.name == workspace)
            )
            ws_id = ws_result.scalar_one_or_none()
            if ws_id is None:
                return f"Workspace '{workspace}' not found."

            result = await session.execute(
                select(Meeting)
                .where(Meeting.workspace_id == ws_id)
                .where(Meeting.started_at >= since)
                .order_by(Meeting.started_at.desc())
            )
            meetings = result.scalars().all()

        if not meetings:
            return f"No meetings in the last {days} days."

        lines = []
        for m in meetings:
            date_str = m.started_at.strftime("%Y-%m-%d %H:%M") if m.started_at else "unknown date"
            participant_list = ", ".join(m.participants) if m.participants else "unknown"
            summary = (m.summary or "No summary.")[:150]
            lines.append(
                f"- **{m.title or 'Untitled'}** ({date_str}) [status: {m.status}]\n"
                f"  Participants: {participant_list}\n"
                f"  Summary: {summary}"
            )

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"list_recent_meetings failed: {e}")
        return f"Failed to list meetings: {e}"


@tool
async def get_action_items(
    status: str = "all", owner: str = "", workspace: str = "personal"
) -> str:
    """List action items from meetings. Filter by status (pending/done/overdue/all) and owner.

    Args:
        status: filter by status - pending, done, overdue, or all (default: all)
        owner: filter by owner name (optional)
        workspace: which workspace (default: personal)
    """
    try:
        from sqlalchemy import select

        from core.db.engine import async_session
        from core.db.models import ActionItem, Meeting, Workspace

        async with async_session() as session:
            ws_result = await session.execute(
                select(Workspace.id).where(Workspace.name == workspace)
            )
            ws_id = ws_result.scalar_one_or_none()
            if ws_id is None:
                return f"Workspace '{workspace}' not found."

            query = select(ActionItem, Meeting.title).outerjoin(
                Meeting, ActionItem.meeting_id == Meeting.id
            ).where(ActionItem.workspace_id == ws_id)

            now = datetime.now(timezone.utc)
            if status == "overdue":
                query = query.where(
                    ActionItem.status == "pending",
                    ActionItem.due_date < now,
                )
            elif status != "all":
                query = query.where(ActionItem.status == status)

            if owner:
                query = query.where(ActionItem.owner.ilike(f"%{owner}%"))

            query = query.order_by(ActionItem.created_at.desc())
            result = await session.execute(query)
            rows = result.all()

        if not rows:
            return "No action items found matching the given filters."

        lines = []
        for item, meeting_title in rows:
            due = item.due_date.strftime("%Y-%m-%d") if item.due_date else "no due date"
            from_meeting = f" (from: {meeting_title})" if meeting_title else ""
            lines.append(
                f"- [{item.status.upper()}] {item.description}\n"
                f"  Owner: {item.owner or 'unassigned'} | Due: {due}{from_meeting}"
            )

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"get_action_items failed: {e}")
        return f"Failed to fetch action items: {e}"


@tool
async def meeting_prep(participants: str, workspace: str = "personal") -> str:
    """Get briefing for an upcoming meeting. Returns past meetings with these participants and their open action items.

    Args:
        participants: comma-separated participant names
        workspace: which workspace (default: personal)
    """
    try:
        from sqlalchemy import select

        from core.db.engine import async_session
        from core.db.models import ActionItem, Meeting, Workspace

        names = [p.strip() for p in participants.split(",") if p.strip()]
        if not names:
            return "Please provide at least one participant name."

        async with async_session() as session:
            ws_result = await session.execute(
                select(Workspace.id).where(Workspace.name == workspace)
            )
            ws_id = ws_result.scalar_one_or_none()
            if ws_id is None:
                return f"Workspace '{workspace}' not found."

            # Query meetings where participants JSONB contains any of the names
            # PostgreSQL ?| operator checks if any key exists in JSONB array
            meeting_result = await session.execute(
                select(Meeting)
                .where(Meeting.workspace_id == ws_id)
                .where(Meeting.participants.cast(
                    __import__("sqlalchemy.dialects.postgresql", fromlist=["JSONB"]).JSONB
                ).op("?|")(names))
                .order_by(Meeting.started_at.desc())
                .limit(5)
            )
            past_meetings = meeting_result.scalars().all()

            # Query open action items for these participants
            action_result = await session.execute(
                select(ActionItem)
                .where(ActionItem.workspace_id == ws_id)
                .where(ActionItem.status == "pending")
                .where(ActionItem.owner.in_(names))
                .order_by(ActionItem.due_date.asc())
            )
            open_items = action_result.scalars().all()

        parts = []

        if past_meetings:
            meeting_lines = []
            for m in past_meetings:
                date_str = m.started_at.strftime("%Y-%m-%d") if m.started_at else "unknown"
                participant_list = ", ".join(m.participants) if m.participants else "unknown"
                summary = (m.summary or "No summary available.")[:200]
                meeting_lines.append(
                    f"- **{m.title or 'Untitled'}** ({date_str})\n"
                    f"  Participants: {participant_list}\n"
                    f"  Summary: {summary}"
                )
            parts.append("**Past Meetings:**\n" + "\n".join(meeting_lines))
        else:
            parts.append("**Past Meetings:** None found with these participants.")

        if open_items:
            item_lines = []
            for item in open_items:
                due = item.due_date.strftime("%Y-%m-%d") if item.due_date else "no due date"
                item_lines.append(
                    f"- {item.description} (Owner: {item.owner or 'unassigned'}, Due: {due})"
                )
            parts.append("**Open Action Items:**\n" + "\n".join(item_lines))
        else:
            parts.append("**Open Action Items:** None.")

        return "\n\n".join(parts)
    except Exception as e:
        logger.error(f"meeting_prep failed: {e}")
        return f"Meeting prep failed: {e}"
