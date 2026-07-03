"""Self-service tools — agent can manage its own configuration."""
from __future__ import annotations

import json
import logging

from langchain_core.tools import tool

logger = logging.getLogger("tools.self_api")


@tool
async def manage_plugin(action: str, plugin_name: str) -> str:
    """Install, uninstall, enable, or disable an MCP plugin.

    Args:
        action: one of 'install', 'uninstall', 'enable', 'disable'
        plugin_name: name of the plugin
    """
    try:
        if action not in ("install", "uninstall", "enable", "disable"):
            return f"Invalid action '{action}'. Must be one of: install, uninstall, enable, disable."

        if action == "install":
            from core.plugins.plugin_loader import install_plugin
            manifest = install_plugin(plugin_name)
            return f"Plugin '{plugin_name}' installed successfully. Description: {manifest.get('description', '')}"

        elif action == "uninstall":
            from core.plugins.plugin_loader import uninstall_plugin
            uninstall_plugin(plugin_name)
            return f"Plugin '{plugin_name}' uninstalled successfully."

        elif action in ("enable", "disable"):
            from sqlalchemy import select

            from core.db.engine import async_session
            from core.db.models import Workspace

            # Apply to the default workspace; agent can be extended to accept workspace param
            from core.config import settings
            workspace_name = settings.default_workspace

            async with async_session() as session:
                result = await session.execute(
                    select(Workspace).where(Workspace.name == workspace_name)
                )
                ws = result.scalar_one_or_none()
                if ws is None:
                    return f"Default workspace '{workspace_name}' not found."

                plugins = list(ws.plugins_enabled or [])
                if action == "enable":
                    if plugin_name not in plugins:
                        plugins.append(plugin_name)
                else:
                    plugins = [p for p in plugins if p != plugin_name]

                ws.plugins_enabled = plugins
                await session.commit()

            return f"Plugin '{plugin_name}' {action}d in workspace '{workspace_name}'."

    except Exception as e:
        logger.error(f"manage_plugin failed: {e}")
        return f"Failed to {action} plugin '{plugin_name}': {e}"


@tool
async def update_settings(settings_json: str) -> str:
    """Update application settings. Pass a JSON string of key:value pairs.

    Args:
        settings_json: JSON string like '{"llm_model": "llama3.2", "stt_gate_mode": "smart"}'
    """
    try:
        updates = json.loads(settings_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"

    try:
        from sqlalchemy import select

        from core.config import settings
        from core.db.engine import async_session
        from core.db.models import AppState

        changed: dict[str, object] = {}
        skipped: list[str] = []

        for field, value in updates.items():
            if hasattr(settings, field):
                try:
                    setattr(settings, field, value)
                    changed[field] = value
                except Exception:
                    skipped.append(field)
            else:
                skipped.append(field)

        if changed:
            async with async_session() as session:
                for key, value in changed.items():
                    result = await session.execute(
                        select(AppState).where(AppState.key == key)
                    )
                    state = result.scalar_one_or_none()
                    if state:
                        state.value = json.dumps(value)
                    else:
                        session.add(AppState(key=key, value=json.dumps(value)))
                await session.commit()

        parts = []
        if changed:
            parts.append(f"Updated: {', '.join(changed.keys())}")
        if skipped:
            parts.append(f"Skipped (unknown fields): {', '.join(skipped)}")
        return ". ".join(parts) if parts else "No changes applied."

    except Exception as e:
        logger.error(f"update_settings failed: {e}")
        return f"Failed to update settings: {e}"


@tool
async def manage_workspace(action: str, name: str, config_json: str = "") -> str:
    """Create, update, or delete a workspace.

    Args:
        action: 'create', 'update', or 'delete'
        name: workspace name
        config_json: JSON config for create/update (optional)
    """
    try:
        if action not in ("create", "update", "delete"):
            return f"Invalid action '{action}'. Must be one of: create, update, delete."

        config: dict = {}
        if config_json:
            try:
                config = json.loads(config_json)
            except json.JSONDecodeError as e:
                return f"Invalid config JSON: {e}"

        from core.db.engine import async_session

        if action == "create":
            from core.workspace import create_workspace
            async with async_session() as session:
                result = await create_workspace(session, name, config or None)
            return f"Workspace '{name}' created successfully."

        elif action == "update":
            from core.workspace import save_config
            async with async_session() as session:
                await save_config(session, name, config)
            return f"Workspace '{name}' updated successfully."

        elif action == "delete":
            from core.workspace import delete_workspace
            async with async_session() as session:
                await delete_workspace(session, name)
            return f"Workspace '{name}' deleted successfully."

    except Exception as e:
        logger.error(f"manage_workspace failed: {e}")
        return f"Failed to {action} workspace '{name}': {e}"
