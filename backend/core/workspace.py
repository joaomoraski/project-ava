"""Workspace management — PostgreSQL backend."""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.models import Workspace

NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-]*$")
RESERVED_NAMES = {"api", "system", "admin", "config", "health"}

DEFAULT_CONFIG: dict[str, Any] = {
    "system_prompt": (
        "You are Ava, a personal AI assistant and rubber duck debugging partner. "
        "You have access to the user's knowledge base, meeting transcripts, todos, "
        "alerts/reminders, calendar, and web search. "
        "Help the user think through problems, organize their work, and stay on top of their schedule. "
        "When the user asks you to create reminders, todos, or search their knowledge, use your tools proactively. "
        "IMPORTANT: Always respond in the same language the user is writing or speaking in. "
        "If they write in Portuguese, respond in Portuguese. If English, respond in English. Match their language exactly."
    ),
    "stt_gate_mode": "smart",
    "proactivity": "medium",
    "tools_enabled": [
        "web_search",
        "knowledge_search",
        "search_meetings",
        "get_action_items",
        "meeting_prep",
        "search_chat_history",
        "manage_todos",
        "manage_alerts",
        "get_calendar_events",
        "get_context",
    ],
    "plugins_enabled": ["context7"],
    "collections": ["notes", "documents"],
    "transcription_priority": {
        "mode": "smart",
        "high_priority_topics": [],
        "low_priority_topics": [],
        "behavior": "",
    },
    "knowledge_seeds": [],
}


def validate_name(name: str) -> None:
    if not NAME_PATTERN.match(name) or len(name) > 64:
        raise ValueError(
            f"Invalid workspace name '{name}'. "
            "Use alphanumeric characters and hyphens (1-64 chars)."
        )
    if name.lower() in RESERVED_NAMES:
        raise ValueError(f"'{name}' is a reserved name.")


async def workspace_exists(session: AsyncSession, name: str) -> bool:
    result = await session.execute(select(Workspace.id).where(Workspace.name == name))
    return result.scalar_one_or_none() is not None


async def load_config(session: AsyncSession, name: str) -> dict[str, Any]:
    result = await session.execute(select(Workspace).where(Workspace.name == name))
    ws = result.scalar_one_or_none()
    if ws is None:
        raise FileNotFoundError(f"Workspace '{name}' not found.")
    return _ws_to_dict(ws)


async def save_config(session: AsyncSession, name: str, config: dict[str, Any]) -> None:
    result = await session.execute(select(Workspace).where(Workspace.name == name))
    ws = result.scalar_one_or_none()
    if ws is None:
        raise FileNotFoundError(f"Workspace '{name}' not found.")
    for key, value in config.items():
        if key not in ("name", "id", "created_at", "updated_at") and hasattr(ws, key):
            setattr(ws, key, value)
    await session.commit()


async def list_workspaces(session: AsyncSession) -> list[str]:
    result = await session.execute(select(Workspace.name).order_by(Workspace.name))
    return [row[0] for row in result.all()]


async def create_workspace(
    session: AsyncSession,
    name: str,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validate_name(name)
    if await workspace_exists(session, name):
        raise ValueError(f"Workspace '{name}' already exists.")

    merged = {**DEFAULT_CONFIG}
    if config:
        merged.update(config)

    ws = Workspace(
        name=name,
        system_prompt=merged["system_prompt"],
        stt_gate_mode=merged["stt_gate_mode"],
        proactivity=merged["proactivity"],
        tools_enabled=merged["tools_enabled"],
        plugins_enabled=merged["plugins_enabled"],
        collections=merged["collections"],
        transcription_priority=merged["transcription_priority"],
        knowledge_seeds=merged["knowledge_seeds"],
    )
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return _ws_to_dict(ws)


async def delete_workspace(session: AsyncSession, name: str) -> None:
    await session.execute(delete(Workspace).where(Workspace.name == name))
    await session.commit()


def _ws_to_dict(ws: Workspace) -> dict[str, Any]:
    return {
        "name": ws.name,
        "system_prompt": ws.system_prompt or "",
        "stt_gate_mode": ws.stt_gate_mode or "smart",
        "proactivity": ws.proactivity or "medium",
        "tools_enabled": ws.tools_enabled or [],
        "plugins_enabled": ws.plugins_enabled or [],
        "collections": ws.collections or [],
        "transcription_priority": ws.transcription_priority or {},
        "knowledge_seeds": ws.knowledge_seeds or [],
    }
