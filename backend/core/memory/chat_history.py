"""Persistent chat history with cross-workspace access and full-text search.

Storage:
  - Messages: JSON files per session in workspaces/{name}/chat_history/{session_id}.json
  - Index: SQLite metadata DB in workspaces/{name}/chat_history/sessions.db

Messages are written immediately after each message (crash-safe).
Sessions are never lost even if the backend crashes mid-response.

ConversationSummaryBufferMemory pattern:
  - Last N messages kept verbatim in context
  - Older messages summarized by LLM (summary stored in session metadata)
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.memory.migrations import check_and_migrate

logger = logging.getLogger("core.memory.chat_history")

WORKSPACES_DIR = "workspaces"
MAX_RECENT_MESSAGES = 20  # keep last N messages verbatim in LLM context


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChatSession:
    """A single conversation session.

    Messages are persisted to disk immediately on append.
    Session metadata is stored in the workspace SQLite index.
    """

    def __init__(
        self,
        session_id: str,
        workspace: str,
        title: str | None = None,
    ) -> None:
        self.session_id = session_id
        self.workspace = workspace
        self.title = title or f"Chat {session_id[:8]}"
        self.created_at: str = _now_iso()
        self.updated_at: str = _now_iso()
        self._messages: list[dict[str, Any]] = []
        self._file_path = self._resolve_path()

    def _resolve_path(self) -> Path:
        path = Path(WORKSPACES_DIR) / self.workspace / "chat_history" / f"{self.session_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def append(self, role: str, content: str) -> dict[str, Any]:
        """Add a message and persist immediately.

        Args:
            role: 'user' or 'assistant'
            content: message text

        Returns:
            The message dict that was saved.
        """
        message = {
            "role": role,
            "content": content,
            "timestamp": _now_iso(),
        }
        self._messages.append(message)
        self.updated_at = message["timestamp"]
        self._save()
        return message

    def _save(self) -> None:
        """Write full session to disk (atomic via temp file)."""
        data = {
            "session_id": self.session_id,
            "workspace": self.workspace,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "messages": self._messages,
        }
        tmp_path = self._file_path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp_path.replace(self._file_path)  # atomic rename
        except Exception as e:
            logger.error(f"Failed to save session {self.session_id}: {e}")
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    @classmethod
    def load(cls, session_id: str, workspace: str) -> "ChatSession | None":
        """Load a session from disk. Returns None if not found."""
        path = Path(WORKSPACES_DIR) / workspace / "chat_history" / f"{session_id}.json"
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            session = cls(
                session_id=data["session_id"],
                workspace=data["workspace"],
                title=data.get("title"),
            )
            session.created_at = data["created_at"]
            session.updated_at = data["updated_at"]
            session._messages = data.get("messages", [])
            return session
        except Exception as e:
            logger.error(f"Failed to load session {session_id}: {e}")
            return None

    @property
    def messages(self) -> list[dict[str, Any]]:
        return list(self._messages)

    def get_context_messages(self, max_recent: int = MAX_RECENT_MESSAGES) -> list[dict[str, Any]]:
        """Return the last N messages for LLM context."""
        return self._messages[-max_recent:]

    def __len__(self) -> int:
        return len(self._messages)


class ChatManager:
    """Manages all chat sessions across workspaces.

    Provides:
    - Session creation and loading
    - Cross-workspace listing and search
    - SQLite-backed index for fast queries
    """

    def __init__(self, workspace: str = "personal") -> None:
        self._workspace = workspace
        self._db_path = self._ensure_db()

    def _ensure_db(self) -> str:
        db_dir = Path(WORKSPACES_DIR) / self._workspace / "chat_history"
        db_dir.mkdir(parents=True, exist_ok=True)
        db_path = str(db_dir / "sessions.db")
        check_and_migrate(db_path)
        return db_path

    def create_session(self, title: str | None = None) -> ChatSession:
        """Create a new chat session and persist it to disk immediately."""
        session_id = str(uuid.uuid4())
        session = ChatSession(session_id=session_id, workspace=self._workspace, title=title)
        session._save()  # ensure file exists even before any messages
        self._index_session(session)
        return session

    def _index_session(self, session: ChatSession) -> None:
        """Insert or update session metadata in SQLite index."""
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO sessions
                        (session_id, workspace, title, created_at, updated_at, message_count)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session.session_id,
                        session.workspace,
                        session.title,
                        session.created_at,
                        session.updated_at,
                        len(session),
                    ),
                )
        except Exception as e:
            logger.error(f"Failed to index session {session.session_id}: {e}")

    def update_session_index(self, session: ChatSession) -> None:
        """Update session metadata after messages are added."""
        self._index_session(session)

    def get_session(self, session_id: str) -> ChatSession | None:
        """Load a session from disk."""
        return ChatSession.load(session_id, self._workspace)

    def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        """List recent sessions for this workspace, newest first."""
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT session_id, workspace, title, created_at, updated_at, message_count
                    FROM sessions
                    WHERE workspace = ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (self._workspace, limit),
                ).fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"list_sessions failed: {e}")
            return []

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Full-text search across all session messages in this workspace.

        Loads session files and scans message content.
        Returns matching messages with session context.
        """
        results = []
        query_lower = query.lower()

        sessions = self.list_sessions(limit=200)
        for session_meta in sessions:
            session = ChatSession.load(session_meta["session_id"], self._workspace)
            if not session:
                continue
            for msg in session.messages:
                if query_lower in msg.get("content", "").lower():
                    results.append({
                        "session_id": session.session_id,
                        "session_title": session.title,
                        "workspace": session.workspace,
                        "role": msg["role"],
                        "content": msg["content"],
                        "timestamp": msg["timestamp"],
                    })
                    if len(results) >= limit:
                        return results

        return results

    def delete_session(self, session_id: str) -> bool:
        """Delete a session from disk and index."""
        path = Path(WORKSPACES_DIR) / self._workspace / "chat_history" / f"{session_id}.json"
        try:
            if path.exists():
                path.unlink()
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            return True
        except Exception as e:
            logger.error(f"delete_session {session_id} failed: {e}")
            return False


class CrossWorkspaceChatManager:
    """Read-only cross-workspace chat access.

    Allows searching and listing sessions across all workspaces.
    """

    @staticmethod
    def list_all_workspaces() -> list[str]:
        """Return all workspace names that have chat history."""
        base = Path(WORKSPACES_DIR)
        if not base.exists():
            return []
        return [
            d.name for d in base.iterdir()
            if d.is_dir() and (d / "chat_history").exists()
        ]

    @classmethod
    def search_all(cls, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search across all workspaces."""
        results = []
        for workspace in cls.list_all_workspaces():
            manager = ChatManager(workspace)
            workspace_results = manager.search(query, limit=limit - len(results))
            results.extend(workspace_results)
            if len(results) >= limit:
                break
        return results

    @classmethod
    def list_all_sessions(cls, limit: int = 50) -> list[dict[str, Any]]:
        """List sessions from all workspaces, sorted by updated_at."""
        all_sessions = []
        for workspace in cls.list_all_workspaces():
            manager = ChatManager(workspace)
            all_sessions.extend(manager.list_sessions(limit=limit))

        all_sessions.sort(key=lambda s: s["updated_at"], reverse=True)
        return all_sessions[:limit]
