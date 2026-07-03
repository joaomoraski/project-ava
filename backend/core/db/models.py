"""SQLAlchemy models — all persistent data lives here."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Text, Integer, Float, Boolean,
    DateTime, Date, ForeignKey, Index, LargeBinary, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, relationship
from pgvector.sqlalchemy import Vector


class Base(DeclarativeBase):
    pass


class Workspace(Base):
    __tablename__ = "workspaces"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(64), unique=True, nullable=False, index=True)
    system_prompt = Column(Text, default="")
    stt_gate_mode = Column(String(20), default="smart")
    proactivity = Column(String(20), default="medium")
    tools_enabled = Column(JSONB, default=list)
    plugins_enabled = Column(JSONB, default=list)
    collections = Column(JSONB, default=list)
    transcription_priority = Column(JSONB, default=dict)
    knowledge_seeds = Column(JSONB, default=list)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    sessions = relationship("ChatSession", back_populates="workspace_rel", cascade="all, delete-orphan")
    documents = relationship("KnowledgeChunk", back_populates="workspace_rel", cascade="all, delete-orphan")


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(255))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    message_count = Column(Integer, default=0)
    summary = Column(Text, nullable=True)
    mode = Column(String(16), nullable=False, default="chat", server_default="chat")
    session_date = Column(Date, nullable=True, default=None)

    workspace_rel = relationship("Workspace", back_populates="sessions")
    messages = relationship(
        "ChatMessage",
        back_populates="session",
        order_by="ChatMessage.created_at",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_sessions_workspace_updated", "workspace_id", "updated_at"),
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(20), nullable=False)  # user, assistant, system
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    session = relationship("ChatSession", back_populates="messages")

    __table_args__ = (
        Index("ix_messages_session_created", "session_id", "created_at"),
        Index(
            "ix_messages_content_fts", "content",
            postgresql_using="gin",
            postgresql_ops={"content": "gin_trgm_ops"},
        ),
    )


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    collection = Column(String(64), default="documents")
    content = Column(Text, nullable=False)
    embedding = Column(Vector(768))  # nomic-embed-text dimension
    source = Column(String(1024), nullable=False)
    source_type = Column(String(20), default="file")  # file, url, meeting
    file_name = Column(String(255), default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    workspace_rel = relationship("Workspace", back_populates="documents")

    __table_args__ = (
        Index("ix_chunks_workspace_source", "workspace_id", "source"),
        Index("ix_chunks_workspace_collection", "workspace_id", "collection"),
        Index(
            "ix_chunks_embedding_hnsw", "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class Secret(Base):
    __tablename__ = "secrets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(128), unique=True, nullable=False, index=True)
    encrypted_value = Column(LargeBinary, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AppState(Base):
    __tablename__ = "app_state"

    key = Column(String(64), primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Meeting(Base):
    __tablename__ = "meetings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(255), default="")
    calendar_event_id = Column(String(255), nullable=True)
    participants = Column(JSONB, default=list)
    transcript = Column(Text, default="")
    summary = Column(Text, nullable=True)
    decisions = Column(JSONB, default=list)
    document_md = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    ended_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(20), default="recording")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ActionItem(Base):
    __tablename__ = "action_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    meeting_id = Column(UUID(as_uuid=True), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=True)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    owner = Column(String(255), nullable=True)
    description = Column(Text, nullable=False)
    status = Column(String(20), default="pending")
    due_date = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    linked_item_id = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Note(Base):
    __tablename__ = "notes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(255), nullable=False)
    content = Column(Text, default="")
    tags = Column(JSONB, default=list)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class GoogleAccount(Base):
    __tablename__ = "google_accounts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False)
    refresh_token_encrypted = Column(LargeBinary, nullable=False)
    scopes = Column(JSONB, default=list)
    label = Column(String(100), default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CalendarEvent(Base):
    __tablename__ = "calendar_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    google_account_id = Column(UUID(as_uuid=True), ForeignKey("google_accounts.id", ondelete="CASCADE"), nullable=False)
    google_event_id = Column(String(255), nullable=False)
    title = Column(String(500), default="")
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=False)
    attendees = Column(JSONB, default=list)
    meet_link = Column(String(500), nullable=True)
    synced_at = Column(DateTime(timezone=True), server_default=func.now())


class Todo(Base):
    __tablename__ = "todos"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True)
    title = Column(String(500), nullable=False)
    description = Column(Text, default="")
    priority = Column(String(20), default="medium")
    status = Column(String(20), default="pending")
    due_date = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Context(Base):
    __tablename__ = "contexts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(128), nullable=False)
    description = Column(Text, default="")
    color = Column(String(7), default="#6366f1")  # hex color
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_contexts_workspace", "workspace_id"),)


class ContextLink(Base):
    __tablename__ = "context_links"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    context_id = Column(UUID(as_uuid=True), ForeignKey("contexts.id", ondelete="CASCADE"), nullable=False)
    item_type = Column(String(32), nullable=False)  # meeting, note, todo, alert, action_item
    item_id = Column(UUID(as_uuid=True), nullable=False)
    auto_linked = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_context_links_context", "context_id"),
        Index("ix_context_links_item", "item_type", "item_id"),
    )


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True)
    title = Column(String(500), nullable=False)
    message = Column(Text, default="")
    trigger_at = Column(DateTime(timezone=True), nullable=False)
    repeat_rule = Column(String(50), default="once")
    status = Column(String(20), default="pending")
    fired_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class JobTask(Base):
    __tablename__ = "job_tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_type = Column(String, nullable=False, index=True)
    target_type = Column(String, nullable=True)
    target_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    workspace_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # queued | running | completed | failed | cancelled
    status = Column(String, nullable=False, default="queued", index=True)
    progress = Column(Float, nullable=False, default=0.0)
    progress_message = Column(String, nullable=True)
    procrastinate_job_id = Column(Integer, nullable=True)  # BigInteger stored as Integer
    error = Column(Text, nullable=True)
    result_payload = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_job_tasks_workspace_status_created", "workspace_id", "status", "created_at"),
    )


class MeetingLink(Base):
    __tablename__ = "meeting_links"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    meeting_id = Column(UUID(as_uuid=True), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False)
    related_meeting_id = Column(UUID(as_uuid=True), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False)
    link_type = Column(String(32), nullable=False, default="related")
    source = Column(String(16), nullable=False, default="user")  # 'user' | 'auto'
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("meeting_id", "related_meeting_id", name="uq_meeting_links_pair"),
        Index("ix_meeting_links_meeting_id", "meeting_id"),
        Index("ix_meeting_links_related_id", "related_meeting_id"),
    )
