"""Tests for the PluginRegistry."""
import pytest
from unittest.mock import MagicMock, patch

from core.plugins.registry import PluginRegistry, BUILTIN_TOOL_MAP, TOOL_GROUPS


class TestPluginRegistryBuiltins:
    def test_list_builtin_tools(self):
        registry = PluginRegistry()
        tools = registry.list_builtin_tools()
        assert isinstance(tools, list)
        assert "web_search" in tools
        assert "get_system_info" in tools
        assert "get_calendar_events" in tools

    def test_list_tool_groups(self):
        registry = PluginRegistry()
        groups = registry.list_tool_groups()
        assert "web_search" in groups
        assert "system_control" in groups
        assert "google_calendar" in groups
        assert "gmail" in groups

    def test_tool_groups_reference_valid_tools(self):
        for group, tools in TOOL_GROUPS.items():
            for tool_name in tools:
                assert tool_name in BUILTIN_TOOL_MAP, (
                    f"Tool '{tool_name}' in group '{group}' not in BUILTIN_TOOL_MAP"
                )

    def test_load_web_search_tool(self):
        registry = PluginRegistry()
        tool = registry._load_builtin_tool("web_search")
        assert tool is not None
        assert tool.name == "web_search"

    def test_load_unknown_tool_returns_none(self):
        registry = PluginRegistry()
        tool = registry._load_builtin_tool("nonexistent_tool_xyz")
        assert tool is None


class TestPluginRegistryWorkspaceTools:
    def test_empty_config_returns_empty_tools(self):
        registry = PluginRegistry()
        tools = registry.get_tools_for_workspace({})
        assert tools == []

    def test_single_builtin_tool_by_name(self):
        registry = PluginRegistry()
        config = {"tools_enabled": ["web_search"]}
        tools = registry.get_tools_for_workspace(config)
        assert len(tools) == 1
        assert tools[0].name == "web_search"

    def test_tool_group_expands(self):
        registry = PluginRegistry()
        config = {"tools_enabled": ["system_control"]}
        tools = registry.get_tools_for_workspace(config)
        tool_names = {t.name for t in tools}
        assert "get_system_info" in tool_names
        assert "open_application" in tool_names
        assert "take_screenshot" in tool_names

    def test_no_duplicate_tools(self):
        registry = PluginRegistry()
        # system_control group + individual tool — should not duplicate
        config = {"tools_enabled": ["system_control", "get_system_info"]}
        tools = registry.get_tools_for_workspace(config)
        tool_names = [t.name for t in tools]
        assert len(tool_names) == len(set(tool_names))

    def test_multiple_groups(self):
        registry = PluginRegistry()
        config = {"tools_enabled": ["web_search", "system_control"]}
        tools = registry.get_tools_for_workspace(config)
        assert len(tools) >= 4  # 1 web_search + 3 system_control

    def test_mcp_tools_included_when_manager_available(self):
        registry = PluginRegistry()
        mock_manager = MagicMock()
        mock_tool = MagicMock()
        mock_tool.name = "todoist_create_task"
        mock_manager.get_tools.return_value = [mock_tool]
        registry.set_mcp_manager(mock_manager)

        config = {"tools_enabled": [], "plugins_enabled": ["todoist"]}
        tools = registry.get_tools_for_workspace(config)
        assert len(tools) == 1
        assert tools[0].name == "todoist_create_task"
        mock_manager.get_tools.assert_called_once_with("todoist")

    def test_mcp_manager_error_is_logged_not_raised(self):
        registry = PluginRegistry()
        mock_manager = MagicMock()
        mock_manager.get_tools.side_effect = Exception("MCP server not running")
        registry.set_mcp_manager(mock_manager)

        config = {"tools_enabled": [], "plugins_enabled": ["todoist"]}
        # Should not raise
        tools = registry.get_tools_for_workspace(config)
        assert tools == []

    def test_no_mcp_manager_skips_plugins(self):
        registry = PluginRegistry()
        # No mcp_manager set
        config = {"tools_enabled": [], "plugins_enabled": ["todoist"]}
        tools = registry.get_tools_for_workspace(config)
        assert tools == []
