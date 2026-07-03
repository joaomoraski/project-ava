"""Plugin loader — loads plugin manifests from the plugins/ directory.

Known presets are bundled here. Custom plugins have their manifest at
plugins/<name>/manifest.json.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger("core.plugins.plugin_loader")

PLUGINS_DIR = "plugins"
INSTALLED_FILE = "plugins/installed.json"

# Known installable presets
KNOWN_PRESETS: dict[str, dict[str, Any]] = {
    "context7": {
        "name": "context7",
        "version": "1.0.0",
        "description": "Live documentation lookup for libraries and frameworks",
        "type": "mcp",
        "mcp_config": {
            "command": "npx",
            "args": ["-y", "@upstash/context7-mcp"],
            "env": {},
        },
        "required_env": [],
        "optional_env": [],
        "default_workspaces": ["work"],
        "tags": ["documentation", "dev-tools"],
    },
    "todoist": {
        "name": "todoist",
        "version": "1.0.0",
        "description": "Task management via Todoist",
        "type": "mcp",
        "mcp_config": {
            "command": "npx",
            "args": ["-y", "@abhiz123/todoist-mcp-server"],
            "env": {"TODOIST_API_TOKEN": "${TODOIST_API_TOKEN}"},
        },
        "required_env": ["TODOIST_API_TOKEN"],
        "optional_env": [],
        "default_workspaces": [],
        "tags": ["productivity", "tasks"],
    },
    "notion": {
        "name": "notion",
        "version": "1.0.0",
        "description": "Read/write Notion pages, databases, blocks",
        "type": "mcp",
        "mcp_config": {
            "command": "npx",
            "args": ["-y", "@notionhq/notion-mcp-server"],
            "env": {"NOTION_API_KEY": "${NOTION_API_KEY}"},
        },
        "required_env": ["NOTION_API_KEY"],
        "optional_env": [],
        "default_workspaces": [],
        "tags": ["notes", "productivity"],
    },
    "github": {
        "name": "github",
        "version": "1.0.0",
        "description": "GitHub repos, issues, pull requests, code search",
        "type": "mcp",
        "mcp_config": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"},
        },
        "required_env": ["GITHUB_TOKEN"],
        "optional_env": [],
        "default_workspaces": [],
        "tags": ["dev-tools", "code"],
    },
    "slack": {
        "name": "slack",
        "version": "1.0.0",
        "description": "Slack messages and channels",
        "type": "mcp",
        "mcp_config": {
            "command": "npx",
            "args": ["-y", "@anthropic/slack-mcp"],
            "env": {"SLACK_BOT_TOKEN": "${SLACK_BOT_TOKEN}"},
        },
        "required_env": ["SLACK_BOT_TOKEN"],
        "optional_env": [],
        "default_workspaces": [],
        "tags": ["communication", "productivity"],
    },
    "linear": {
        "name": "linear",
        "version": "1.0.0",
        "description": "Linear issues, projects, and cycles",
        "type": "mcp",
        "mcp_config": {
            "command": "npx",
            "args": ["-y", "@anthropic/linear-mcp"],
            "env": {"LINEAR_API_KEY": "${LINEAR_API_KEY}"},
        },
        "required_env": ["LINEAR_API_KEY"],
        "optional_env": [],
        "default_workspaces": [],
        "tags": ["project-management", "dev-tools"],
    },
    "brave-search": {
        "name": "brave-search",
        "version": "1.0.0",
        "description": "Web search via Brave (free tier available)",
        "type": "mcp",
        "mcp_config": {
            "command": "npx",
            "args": ["-y", "@anthropic/brave-search-mcp"],
            "env": {"BRAVE_API_KEY": "${BRAVE_API_KEY}"},
        },
        "required_env": ["BRAVE_API_KEY"],
        "optional_env": [],
        "default_workspaces": [],
        "tags": ["search", "web"],
    },
    "filesystem": {
        "name": "filesystem",
        "version": "1.0.0",
        "description": "Scoped local filesystem access",
        "type": "mcp",
        "mcp_config": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "--root", "/home/user/documents"],
            "env": {},
        },
        "required_env": [],
        "optional_env": [],
        "default_workspaces": [],
        "tags": ["filesystem", "files"],
    },
    "postgres": {
        "name": "postgres",
        "version": "1.0.0",
        "description": "Query PostgreSQL databases",
        "type": "mcp",
        "mcp_config": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-postgres"],
            "env": {"DATABASE_URL": "${DATABASE_URL}"},
        },
        "required_env": ["DATABASE_URL"],
        "optional_env": [],
        "default_workspaces": [],
        "tags": ["database"],
    },
    "spotify": {
        "name": "spotify",
        "version": "1.0.0",
        "description": "Spotify playback control, playlists, and search",
        "type": "mcp",
        "mcp_config": {
            "command": "npx",
            "args": ["-y", "@anthropic/spotify-mcp"],
            "env": {
                "SPOTIFY_CLIENT_ID": "${SPOTIFY_CLIENT_ID}",
                "SPOTIFY_CLIENT_SECRET": "${SPOTIFY_CLIENT_SECRET}",
            },
        },
        "required_env": ["SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET"],
        "optional_env": [],
        "default_workspaces": [],
        "tags": ["music", "entertainment"],
    },
}


def load_installed() -> dict[str, Any]:
    """Load the installed plugins index."""
    if not os.path.exists(INSTALLED_FILE):
        return {"plugins": {}}
    try:
        with open(INSTALLED_FILE) as f:
            return json.load(f)
    except Exception:
        return {"plugins": {}}


def save_installed(data: dict[str, Any]) -> None:
    os.makedirs(PLUGINS_DIR, exist_ok=True)
    with open(INSTALLED_FILE, "w") as f:
        json.dump(data, f, indent=2)


def load_manifest(plugin_name: str) -> dict[str, Any] | None:
    """Load plugin manifest from disk or known presets."""
    # Check known presets first
    if plugin_name in KNOWN_PRESETS:
        return dict(KNOWN_PRESETS[plugin_name])

    # Check custom plugin directory
    manifest_path = os.path.join(PLUGINS_DIR, plugin_name, "manifest.json")
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path) as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load manifest for '{plugin_name}': {e}")
    return None


def install_plugin(name: str, custom_config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Install a plugin by name (preset) or custom MCP config.

    Returns the manifest of the installed plugin.
    """
    if custom_config:
        manifest = {
            "name": name,
            "version": "1.0.0",
            "description": custom_config.get("description", "Custom MCP server"),
            "type": "mcp",
            "mcp_config": {
                "command": custom_config.get("command", "npx"),
                "args": custom_config.get("args", []),
                "env": custom_config.get("env", {}),
            },
            "required_env": [],
            "optional_env": [],
            "default_workspaces": [],
            "tags": ["custom"],
        }
        # Save manifest to disk
        plugin_dir = os.path.join(PLUGINS_DIR, name)
        os.makedirs(plugin_dir, exist_ok=True)
        with open(os.path.join(plugin_dir, "manifest.json"), "w") as f:
            json.dump(manifest, f, indent=2)
    else:
        manifest = load_manifest(name)
        if not manifest:
            raise ValueError(f"Unknown plugin: '{name}'. Use a known preset or provide custom config.")

    # Register with MCP manager
    from core.plugins.mcp_manager import mcp_manager
    mcp_config = manifest.get("mcp_config", {})
    mcp_manager.register_server(name, {
        "command": mcp_config.get("command", "npx"),
        "args": mcp_config.get("args", []),
        "env": mcp_config.get("env", {}),
        "description": manifest.get("description", ""),
        "enabled": True,
        "auto_start": False,
    })

    # Update installed index
    data = load_installed()
    data["plugins"][name] = {
        "version": manifest.get("version", "1.0.0"),
        "status": "installed",
        "installed_at": __import__("datetime").datetime.now().isoformat(),
    }
    save_installed(data)

    logger.info(f"Plugin '{name}' installed.")
    return manifest


def uninstall_plugin(name: str) -> bool:
    """Uninstall a plugin."""
    from core.plugins.mcp_manager import mcp_manager
    mcp_manager.unregister_server(name)

    data = load_installed()
    if name in data["plugins"]:
        del data["plugins"][name]
        save_installed(data)

    # Remove custom manifest if present
    manifest_path = os.path.join(PLUGINS_DIR, name, "manifest.json")
    if os.path.exists(manifest_path):
        import shutil
        shutil.rmtree(os.path.join(PLUGINS_DIR, name), ignore_errors=True)

    logger.info(f"Plugin '{name}' uninstalled.")
    return True


def list_installed() -> list[dict[str, Any]]:
    """Return list of installed plugins with manifests."""
    data = load_installed()
    result = []
    for name, info in data["plugins"].items():
        manifest = load_manifest(name) or {}
        result.append({
            "name": name,
            "version": info.get("version", "?"),
            "description": manifest.get("description", ""),
            "type": manifest.get("type", "mcp"),
            "tags": manifest.get("tags", []),
            "required_env": manifest.get("required_env", []),
            "installed": True,
            "installed_at": info.get("installed_at"),
        })
    return result


def list_available() -> list[dict[str, Any]]:
    """Return all known installable presets."""
    installed = {p["name"] for p in list_installed()}
    return [
        {
            "name": name,
            "description": preset.get("description", ""),
            "tags": preset.get("tags", []),
            "required_env": preset.get("required_env", []),
            "installed": name in installed,
        }
        for name, preset in KNOWN_PRESETS.items()
    ]
