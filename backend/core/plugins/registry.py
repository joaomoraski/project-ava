"""Central plugin and tool registry.

Discovers built-in tools and MCP-based plugins, assembles workspace tool sets.
"""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import BaseTool

logger = logging.getLogger("core.plugins.registry")

# Map of built-in tool name → lazy import path
BUILTIN_TOOL_MAP: dict[str, tuple[str, str]] = {
    "web_search": ("tools.web_search", "web_search"),
    "get_system_info": ("tools.system_control", "get_system_info"),
    "open_application": ("tools.system_control", "open_application"),
    "take_screenshot": ("tools.system_control", "take_screenshot"),
    "get_calendar_events": ("tools.google_calendar", "get_calendar_events"),
    "create_calendar_event": ("tools.google_calendar", "create_calendar_event"),
    "get_recent_emails": ("tools.gmail", "get_recent_emails"),
    "send_email": ("tools.gmail", "send_email"),
    "search_chat_history": ("tools.chat_search", "search_chat_history"),
    "trigger_animation": ("tools.animations", "trigger_animation"),
}

# Logical groupings for convenience in workspace config
TOOL_GROUPS: dict[str, list[str]] = {
    "web_search": ["web_search"],
    "system_control": ["get_system_info", "open_application", "take_screenshot"],
    "google_calendar": ["get_calendar_events", "create_calendar_event"],
    "gmail": ["get_recent_emails", "send_email"],
    "chat_search": ["search_chat_history"],
    "animations": ["trigger_animation"],
}


class PluginRegistry:
    """Central registry for built-in tools and MCP plugins.

    Assembles the final tool set for each workspace based on its config.
    """

    def __init__(self) -> None:
        self._mcp_manager = None

    def set_mcp_manager(self, mcp_manager) -> None:
        self._mcp_manager = mcp_manager

    def _load_builtin_tool(self, tool_name: str) -> BaseTool | None:
        """Dynamically import and return a built-in tool."""
        if tool_name not in BUILTIN_TOOL_MAP:
            logger.warning(f"Unknown built-in tool: {tool_name}")
            return None

        module_path, attr_name = BUILTIN_TOOL_MAP[tool_name]
        try:
            import importlib
            module = importlib.import_module(module_path)
            return getattr(module, attr_name)
        except Exception as e:
            logger.error(f"Failed to load tool {tool_name}: {e}")
            return None

    def get_tools_for_workspace(self, workspace_config: dict[str, Any]) -> list[BaseTool]:
        """Build the tool set for a workspace.

        Combines:
        1. Built-in tools listed in workspace config `tools_enabled`
        2. MCP tools from `plugins_enabled` (if MCP manager is available)

        Args:
            workspace_config: workspace config dict (loaded from config.json)

        Returns:
            List of LangChain BaseTool instances ready for injection into the agent.
        """
        tools: list[BaseTool] = []
        seen: set[str] = set()

        # 1. Built-in tools
        tools_enabled: list[str] = workspace_config.get("tools_enabled", [])
        for group_or_tool in tools_enabled:
            # Support both individual tool names and group names
            tool_names = TOOL_GROUPS.get(group_or_tool, [group_or_tool])
            for tool_name in tool_names:
                if tool_name in seen:
                    continue
                t = self._load_builtin_tool(tool_name)
                if t:
                    tools.append(t)
                    seen.add(tool_name)

        # 2. MCP tools from enabled plugins
        if self._mcp_manager:
            plugins_enabled: list[str] = workspace_config.get("plugins_enabled", [])
            for plugin_name in plugins_enabled:
                try:
                    mcp_tools = self._mcp_manager.get_tools(plugin_name)
                    for t in mcp_tools:
                        if t.name not in seen:
                            tools.append(t)
                            seen.add(t.name)
                except Exception as e:
                    logger.warning(f"Failed to get tools from plugin {plugin_name}: {e}")

        logger.debug(f"Assembled {len(tools)} tools for workspace: {[t.name for t in tools]}")
        return tools

    def list_builtin_tools(self) -> list[str]:
        """Return all available built-in tool names."""
        return list(BUILTIN_TOOL_MAP.keys())

    def list_tool_groups(self) -> dict[str, list[str]]:
        """Return tool group → tool name mappings."""
        return dict(TOOL_GROUPS)


# Global singleton
plugin_registry = PluginRegistry()
