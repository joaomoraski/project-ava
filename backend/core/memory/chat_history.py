"""Persistent chat history — PostgreSQL backend.

All messages are stored immediately on append (crash-safe via DB transactions).
Full-text search uses PostgreSQL pg_trgm GIN index.
Cross-workspace queries are single SQL statements.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select, delete, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.models import ChatSession as ChatSessionModel, ChatMessage, Workspace

logger = logging.getLogger("core.memory.chat_history")

MAX_RECENT_MESSAGES = 20


class ChatSession:
    """A single conversation session backed by PostgreSQL."""

    def __init__(
        self,
        session_id: str,
        workspace: str,
        title: str | None = None,
        mode: str = "chat",
        session_date: date | None = None,
    ) -> None:
        self.session_id = session_id
        self.workspace = workspace
        self.title = title or f"Chat {session_id[:8]}"
        self.mode = mode
        self.session_date = session_date
        self.created_at: str = datetime.now(timezone.utc).isoformat()
        self.updated_at: str = self.created_at
        self._messages: list[dict[str, Any]] = []

    async def append(self, session: AsyncSession, role: str, content: str) -> dict[str, Any]:
        """Add a message — persisted immediately via DB insert."""
        now = datetime.now(timezone.utc)
        msg = ChatMessage(
            id=uuid.uuid4(),
            session_id=uuid.UUID(self.session_id),
            role=role,
            content=content,
            created_at=now,
        )
        session.add(msg)

        # Update session metadata
        await session.execute(
            update(ChatSessionModel)
            .where(ChatSessionModel.id == uuid.UUID(self.session_id))
            .values(
                updated_at=now,
                message_count=ChatSessionModel.message_count + 1,
            )
        )
        await session.commit()

        message_dict = {
            "role": role,
            "content": content,
            "timestamp": now.isoformat(),
        }
        self._messages.append(message_dict)
        self.updated_at = now.isoformat()
        return message_dict

    @classmethod
    async def load(cls, session: AsyncSession, session_id: str, workspace: str) -> "ChatSession | None":
        """Load a session with all messages from DB."""
        result = await session.execute(
            select(ChatSessionModel)
            .join(Workspace)
            .where(
                ChatSessionModel.id == uuid.UUID(session_id),
                Workspace.name == workspace,
            )
        )
        db_session = result.scalar_one_or_none()
        if db_session is None:
            return None

        cs = cls(
            session_id=str(db_session.id),
            workspace=workspace,
            title=db_session.title,
            mode=db_session.mode or "chat",
            session_date=db_session.session_date,
        )
        cs.created_at = db_session.created_at.isoformat() if db_session.created_at else cs.created_at
        cs.updated_at = db_session.updated_at.isoformat() if db_session.updated_at else cs.updated_at

        # Load messages
        msg_result = await session.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == db_session.id)
            .order_by(ChatMessage.created_at)
        )
        cs._messages = [
            {
                "role": m.role,
                "content": m.content,
                "timestamp": m.created_at.isoformat() if m.created_at else "",
            }
            for m in msg_result.scalars().all()
        ]
        return cs

    @property
    def messages(self) -> list[dict[str, Any]]:
        return list(self._messages)

    def get_context_messages(self, max_recent: int = MAX_RECENT_MESSAGES) -> list[dict[str, Any]]:
        return self._messages[-max_recent:]

    def __len__(self) -> int:
        return len(self._messages)


class ChatManager:
    """Manages chat sessions for a workspace — PostgreSQL backend."""

    def __init__(self, workspace: str = "personal") -> None:
        self._workspace = workspace

    async def _get_workspace_id(self, session: AsyncSession) -> uuid.UUID | None:
        result = await session.execute(
            select(Workspace.id).where(Workspace.name == self._workspace)
        )
        return result.scalar_one_or_none()

    async def create_session(
        self,
        session: AsyncSession,
        title: str | None = None,
        mode: str = "chat",
        session_date: date | None = None,
    ) -> ChatSession:
        """Create a new chat session."""
        workspace_id = await self._get_workspace_id(session)
        if workspace_id is None:
            raise ValueError(f"Workspace '{self._workspace}' not found.")

        session_id = uuid.uuid4()
        cs_title = title or f"Chat {str(session_id)[:8]}"

        db_session = ChatSessionModel(
            id=session_id,
            workspace_id=workspace_id,
            title=cs_title,
            message_count=0,
            mode=mode,
            session_date=session_date,
        )
        session.add(db_session)
        await session.commit()
        await session.refresh(db_session)

        cs = ChatSession(
            session_id=str(session_id),
            workspace=self._workspace,
            title=cs_title,
            mode=mode,
            session_date=session_date,
        )
        cs.created_at = db_session.created_at.isoformat() if db_session.created_at else cs.created_at
        cs.updated_at = cs.created_at
        return cs

    async def get_or_create_daily_companion_session(
        self,
        session: AsyncSession,
        day: date | None = None,
    ) -> ChatSession:
        """Return today's companion session for this workspace, creating if missing."""
        day = day or date.today()

        workspace_id = await self._get_workspace_id(session)
        if workspace_id is None:
            raise ValueError(f"Workspace '{self._workspace}' not found.")

        result = await session.execute(
            select(ChatSessionModel).where(
                ChatSessionModel.workspace_id == workspace_id,
                ChatSessionModel.mode == "companion",
                ChatSessionModel.session_date == day,
            )
        )
        db_session = result.scalar_one_or_none()

        if db_session is not None:
            cs = ChatSession(
                session_id=str(db_session.id),
                workspace=self._workspace,
                title=db_session.title,
                mode="companion",
                session_date=db_session.session_date,
            )
            cs.created_at = db_session.created_at.isoformat() if db_session.created_at else cs.created_at
            cs.updated_at = db_session.updated_at.isoformat() if db_session.updated_at else cs.updated_at
            return cs

        return await self.create_session(
            session,
            title=f"Companion — {day.isoformat()}",
            mode="companion",
            session_date=day,
        )

    async def finalize_companion_session(
        self,
        session: AsyncSession,
        session_id: str,
        llm: Any | None = None,
    ) -> None:
        """Set updated_at=now, optionally regenerate title via LLM from first 10 messages."""
        now = datetime.now(timezone.utc)

        cs = await ChatSession.load(session, session_id, self._workspace)
        if cs is None:
            logger.warning("finalize_companion_session: session %s not found", session_id)
            return

        new_title: str | None = None
        if llm is not None and len(cs) >= 6:
            first_ten = cs.messages[:10]
            transcript = "\n".join(f"{m['role']}: {m['content']}" for m in first_ten)
            prompt = (
                "Summarize this companion conversation in 4-6 words for a title:\n"
                f"{transcript}"
            )
            try:
                response = await llm.ainvoke(prompt)
                raw = response.content if hasattr(response, "content") else str(response)
                candidate = raw.strip().strip('"').strip("'")
                if candidate:
                    new_title = candidate
            except Exception as exc:
                logger.warning("finalize_companion_session: LLM title generation failed: %s", exc)

        values: dict[str, Any] = {"updated_at": now}
        if new_title:
            values["title"] = new_title

        await session.execute(
            update(ChatSessionModel)
            .where(ChatSessionModel.id == uuid.UUID(session_id))
            .values(**values)
        )
        await session.commit()

        if new_title:
            cs.title = new_title
        cs.updated_at = now.isoformat()

    async def get_session(self, session: AsyncSession, session_id: str) -> ChatSession | None:
        return await ChatSession.load(session, session_id, self._workspace)

    async def list_sessions(self, session: AsyncSession, limit: int = 50) -> list[dict[str, Any]]:
        result = await session.execute(
            select(ChatSessionModel)
            .join(Workspace)
            .where(Workspace.name == self._workspace)
            .order_by(ChatSessionModel.updated_at.desc())
            .limit(limit)
        )
        return [
            {
                "session_id": str(s.id),
                "workspace": self._workspace,
                "title": s.title,
                "created_at": s.created_at.isoformat() if s.created_at else "",
                "updated_at": s.updated_at.isoformat() if s.updated_at else "",
                "message_count": s.message_count or 0,
            }
            for s in result.scalars().all()
        ]

    async def search(self, session: AsyncSession, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Full-text search using pg_trgm index."""
        result = await session.execute(
            select(
                ChatMessage.content,
                ChatMessage.role,
                ChatMessage.created_at,
                ChatSessionModel.id.label("session_id"),
                ChatSessionModel.title.label("session_title"),
            )
            .join(ChatSessionModel, ChatMessage.session_id == ChatSessionModel.id)
            .join(Workspace, ChatSessionModel.workspace_id == Workspace.id)
            .where(
                Workspace.name == self._workspace,
                ChatMessage.content.ilike(f"%{query}%"),
            )
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
        )
        return [
            {
                "session_id": str(row.session_id),
                "session_title": row.session_title,
                "workspace": self._workspace,
                "role": row.role,
                "content": row.content,
                "timestamp": row.created_at.isoformat() if row.created_at else "",
            }
            for row in result.all()
        ]

    async def delete_session(self, session: AsyncSession, session_id: str) -> bool:
        try:
            await session.execute(
                delete(ChatSessionModel).where(ChatSessionModel.id == uuid.UUID(session_id))
            )
            await session.commit()
            return True
        except Exception as e:
            logger.error(f"delete_session {session_id} failed: {e}")
            return False


class CrossWorkspaceChatManager:
    """Cross-workspace chat access — single queries, no iteration."""

    @staticmethod
    async def list_all_sessions(session: AsyncSession, limit: int = 50) -> list[dict[str, Any]]:
        result = await session.execute(
            select(
                ChatSessionModel.id,
                ChatSessionModel.title,
                ChatSessionModel.created_at,
                ChatSessionModel.updated_at,
                ChatSessionModel.message_count,
                Workspace.name.label("workspace"),
            )
            .join(Workspace, ChatSessionModel.workspace_id == Workspace.id)
            .order_by(ChatSessionModel.updated_at.desc())
            .limit(limit)
        )
        return [
            {
                "session_id": str(row.id),
                "workspace": row.workspace,
                "title": row.title,
                "created_at": row.created_at.isoformat() if row.created_at else "",
                "updated_at": row.updated_at.isoformat() if row.updated_at else "",
                "message_count": row.message_count or 0,
            }
            for row in result.all()
        ]

    @staticmethod
    async def search_all(session: AsyncSession, query: str, limit: int = 20) -> list[dict[str, Any]]:
        result = await session.execute(
            select(
                ChatMessage.content,
                ChatMessage.role,
                ChatMessage.created_at,
                ChatSessionModel.id.label("session_id"),
                ChatSessionModel.title.label("session_title"),
                Workspace.name.label("workspace"),
            )
            .join(ChatSessionModel, ChatMessage.session_id == ChatSessionModel.id)
            .join(Workspace, ChatSessionModel.workspace_id == Workspace.id)
            .where(ChatMessage.content.ilike(f"%{query}%"))
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
        )
        return [
            {
                "session_id": str(row.session_id),
                "session_title": row.session_title,
                "workspace": row.workspace,
                "role": row.role,
                "content": row.content,
                "timestamp": row.created_at.isoformat() if row.created_at else "",
            }
            for row in result.all()
        ]

    @staticmethod
    async def list_all_workspaces(session: AsyncSession) -> list[str]:
        result = await session.execute(select(Workspace.name).order_by(Workspace.name))
        return [row[0] for row in result.all()]
