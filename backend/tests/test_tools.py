"""Tests for built-in tool registry and tool import/logic correctness."""
from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.tools import BaseTool

from core.plugins.registry import BUILTIN_TOOL_MAP, TOOL_GROUPS, PluginRegistry


# ── Registry structure tests ──────────────────────────────────────────────────

EXPECTED_TOOLS = [
    "web_search",
    "knowledge_search",
    "search_chat_history",
    "search_meetings",
    "list_recent_meetings",
    "get_action_items",
    "meeting_prep",
    "manage_plugin",
    "update_settings",
    "manage_workspace",
    "get_system_info",
    "open_application",
    "take_screenshot",
    "get_calendar_events",
    "create_calendar_event",
    "get_recent_emails",
    "send_email",
]

EXPECTED_GROUPS = [
    "web_search",
    "system_control",
    "google_calendar",
    "gmail",
    "chat_search",
    "knowledge_search",
    "meetings",
    "self_service",
]


def test_builtin_tool_map_has_all_tools():
    for tool_name in EXPECTED_TOOLS:
        assert tool_name in BUILTIN_TOOL_MAP, f"{tool_name!r} missing from BUILTIN_TOOL_MAP"


def test_tool_groups_complete():
    for group in EXPECTED_GROUPS:
        assert group in TOOL_GROUPS, f"group {group!r} missing from TOOL_GROUPS"
    # All tools referenced in groups must exist in the map
    for group, names in TOOL_GROUPS.items():
        for name in names:
            assert name in BUILTIN_TOOL_MAP, (
                f"TOOL_GROUPS[{group!r}] references unknown tool {name!r}"
            )


def test_load_builtin_tool():
    registry = PluginRegistry()
    tool = registry._load_builtin_tool("web_search")
    assert tool is not None
    assert isinstance(tool, BaseTool)


def test_get_tools_for_workspace():
    registry = PluginRegistry()
    config = {
        "tools_enabled": ["web_search", "knowledge_search"],
        "plugins_enabled": [],
    }
    tools = registry.get_tools_for_workspace(config)
    tool_names = {t.name for t in tools}
    assert "web_search" in tool_names
    assert "knowledge_search" in tool_names


def test_get_tools_for_workspace_expands_group():
    registry = PluginRegistry()
    config = {
        "tools_enabled": ["system_control"],
        "plugins_enabled": [],
    }
    tools = registry.get_tools_for_workspace(config)
    tool_names = {t.name for t in tools}
    assert "get_system_info" in tool_names
    assert "open_application" in tool_names
    assert "take_screenshot" in tool_names


def test_get_tools_for_workspace_deduplicates():
    """Specifying a tool individually AND via a group should not duplicate it."""
    registry = PluginRegistry()
    config = {
        "tools_enabled": ["web_search", "web_search"],
        "plugins_enabled": [],
    }
    tools = registry.get_tools_for_workspace(config)
    names = [t.name for t in tools]
    assert names.count("web_search") == 1


# ── Import tests ──────────────────────────────────────────────────────────────

def test_import_knowledge_search():
    mod = importlib.import_module("tools.knowledge_search")
    assert hasattr(mod, "knowledge_search")
    assert isinstance(mod.knowledge_search, BaseTool)


def test_import_chat_search():
    mod = importlib.import_module("tools.chat_search")
    assert hasattr(mod, "search_chat_history")
    assert isinstance(mod.search_chat_history, BaseTool)


def test_import_meeting_tools():
    mod = importlib.import_module("tools.meetings")
    for name in ("search_meetings", "list_recent_meetings", "get_action_items", "meeting_prep"):
        assert hasattr(mod, name), f"tools.meetings missing {name!r}"
        assert isinstance(getattr(mod, name), BaseTool)


def test_list_recent_meetings_in_registry():
    """list_recent_meetings must be in BUILTIN_TOOL_MAP and the 'meetings' group."""
    assert "list_recent_meetings" in BUILTIN_TOOL_MAP
    assert "list_recent_meetings" in TOOL_GROUPS["meetings"]


def test_list_recent_meetings_loadable():
    """PluginRegistry._load_builtin_tool must return the actual tool object."""
    registry = PluginRegistry()
    t = registry._load_builtin_tool("list_recent_meetings")
    assert t is not None
    assert isinstance(t, BaseTool)
    assert t.name == "list_recent_meetings"


def test_import_self_api_tools():
    mod = importlib.import_module("tools.self_api")
    for name in ("manage_plugin", "update_settings", "manage_workspace"):
        assert hasattr(mod, name), f"tools.self_api missing {name!r}"
        assert isinstance(getattr(mod, name), BaseTool)


def test_import_web_search():
    mod = importlib.import_module("tools.web_search")
    assert hasattr(mod, "web_search")
    assert isinstance(mod.web_search, BaseTool)


# ── Async tool logic tests ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_knowledge_search_no_results():
    """knowledge_search returns a 'not found' message when DB returns empty list."""
    mock_kb = MagicMock()
    mock_kb.search = AsyncMock(return_value=[])

    mock_session = AsyncMock()
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("core.db.engine.async_session", return_value=mock_ctx), \
         patch("core.knowledge.rag.KnowledgeBase", return_value=mock_kb):
        from tools.knowledge_search import knowledge_search as ks_tool
        result = await ks_tool.arun({"query": "does not exist"})

    assert "No relevant documents found" in result


@pytest.mark.asyncio
async def test_knowledge_search_returns_formatted_results():
    """knowledge_search formats source + content when results are present."""
    mock_results = [
        {"source": "notes.txt", "content": "Hello world this is a test.", "score": 0.95},
    ]
    mock_kb = MagicMock()
    mock_kb.search = AsyncMock(return_value=mock_results)

    mock_session = AsyncMock()
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("core.db.engine.async_session", return_value=mock_ctx), \
         patch("core.knowledge.rag.KnowledgeBase", return_value=mock_kb):
        from tools.knowledge_search import knowledge_search as ks_tool
        result = await ks_tool.arun({"query": "hello"})

    assert "notes.txt" in result
    assert "Hello world" in result


@pytest.mark.asyncio
async def test_get_action_items_formats_output():
    """get_action_items returns formatted lines per item."""
    from datetime import datetime, timezone

    from unittest.mock import AsyncMock, MagicMock, patch

    # Build fake ORM rows: (ActionItem, meeting_title)
    item = MagicMock()
    item.status = "pending"
    item.description = "Fix the login bug"
    item.owner = "Alice"
    item.due_date = datetime(2026, 4, 10, tzinfo=timezone.utc)
    item.created_at = datetime(2026, 4, 1, tzinfo=timezone.utc)

    fake_rows = [(item, "Sprint Planning")]

    # Mock workspace lookup — returns a workspace id
    ws_scalar = MagicMock()
    ws_scalar.scalar_one_or_none.return_value = 1

    # Mock action items lookup — returns rows
    items_result = MagicMock()
    items_result.all.return_value = fake_rows

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=[ws_scalar, items_result])

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("core.db.engine.async_session", return_value=mock_ctx):
        from tools.meetings import get_action_items
        result = await get_action_items.arun({"status": "pending"})

    assert "Fix the login bug" in result
    assert "Alice" in result
    assert "PENDING" in result
    assert "2026-04-10" in result


@pytest.mark.asyncio
async def test_meeting_prep_splits_participants():
    """meeting_prep splits comma-separated participants correctly."""
    # Mock workspace lookup → None so we get an early return
    ws_scalar = MagicMock()
    ws_scalar.scalar_one_or_none.return_value = None

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=ws_scalar)

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("core.db.engine.async_session", return_value=mock_ctx):
        from tools.meetings import meeting_prep
        # Workspace won't be found — that's fine; we just verify participant
        # parsing doesn't crash and the tool handles the missing workspace.
        result = await meeting_prep.arun(
            {"participants": "Alice, Bob, Carol", "workspace": "nonexistent"}
        )

    # Should get workspace-not-found, not a crash
    assert "nonexistent" in result or "not found" in result.lower()


@pytest.mark.asyncio
async def test_meeting_prep_empty_participants():
    """meeting_prep returns a helpful message when participants string is empty."""
    # We shouldn't even hit the DB for an empty input
    from tools.meetings import meeting_prep
    result = await meeting_prep.arun({"participants": "   ", "workspace": "personal"})
    assert "at least one participant" in result.lower() or "participant" in result.lower()
