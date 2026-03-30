"""Ava CLI — workspace and plugin management.

Usage:
    python cli.py workspace list
    python cli.py workspace create <name>
    python cli.py workspace edit <name>
    python cli.py plugin list
    python cli.py plugin install <name>
    python cli.py plugin config <name>
    python cli.py status
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def cmd_workspace_list() -> None:
    ws_dir = "workspaces"
    if not os.path.exists(ws_dir):
        print("No workspaces found.")
        return
    workspaces = [d for d in os.listdir(ws_dir) if os.path.isdir(os.path.join(ws_dir, d))]
    if not workspaces:
        print("No workspaces found.")
        return
    print(f"{'Name':<20} {'STT Gate':<12} {'Plugins'}")
    print("-" * 60)
    for name in sorted(workspaces):
        config_path = os.path.join(ws_dir, name, "config.json")
        if os.path.exists(config_path):
            with open(config_path) as f:
                cfg = json.load(f)
            plugins = ", ".join(cfg.get("plugins_enabled", [])) or "none"
            gate = cfg.get("stt_gate_mode", "smart")
        else:
            plugins = "—"
            gate = "—"
        print(f"{name:<20} {gate:<12} {plugins}")


def cmd_workspace_create(name: str) -> None:
    import re
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9\-]*$", name):
        print(f"Error: workspace name '{name}' is invalid. Use alphanumeric characters and hyphens only.")
        sys.exit(1)

    ws_path = f"workspaces/{name}"
    config_path = f"{ws_path}/config.json"

    if os.path.exists(config_path):
        print(f"Error: workspace '{name}' already exists.")
        sys.exit(1)

    os.makedirs(f"{ws_path}/chroma_db", exist_ok=True)
    os.makedirs(f"{ws_path}/chat_history", exist_ok=True)

    config = {
        "name": name,
        "system_prompt": "",
        "stt_gate_mode": "smart",
        "proactivity": "medium",
        "tools_enabled": ["web_search"],
        "plugins_enabled": [],
        "collections": ["notes", "documents"],
        "transcription_priority": {
            "mode": "smart",
            "high_priority_topics": [],
            "low_priority_topics": [],
            "behavior": "",
        },
        "knowledge_seeds": [],
    }
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    print(f"Workspace '{name}' created at {ws_path}/")


def cmd_workspace_edit(name: str) -> None:
    config_path = f"workspaces/{name}/config.json"
    if not os.path.exists(config_path):
        print(f"Error: workspace '{name}' not found.")
        sys.exit(1)
    editor = os.environ.get("EDITOR", "nano")
    os.execvp(editor, [editor, config_path])


def cmd_plugin_list() -> None:
    installed_path = "plugins/installed.json"
    if not os.path.exists(installed_path):
        print("No plugins installed.")
        return
    with open(installed_path) as f:
        data = json.load(f)
    plugins = data.get("plugins", {})
    if not plugins:
        print("No plugins installed.")
        return
    print(f"{'Name':<20} {'Version':<10} {'Status'}")
    print("-" * 50)
    for name, info in sorted(plugins.items()):
        version = info.get("version", "—")
        status = info.get("status", "stopped")
        print(f"{name:<20} {version:<10} {status}")


def cmd_status() -> None:
    """Quick status check — useful without the frontend running."""
    import httpx
    from core.config import settings
    try:
        resp = httpx.get(f"http://localhost:{settings.api_port}/health", timeout=3)
        data = resp.json()
        print(f"Backend: running (v{data.get('version', '?')})")
    except Exception:
        print("Backend: not running")


def main() -> None:
    parser = argparse.ArgumentParser(prog="ava", description="Ava Project CLI")
    subparsers = parser.add_subparsers(dest="command")

    # workspace
    ws_parser = subparsers.add_parser("workspace", help="Workspace management")
    ws_sub = ws_parser.add_subparsers(dest="ws_command")
    ws_sub.add_parser("list", help="List all workspaces")
    ws_create = ws_sub.add_parser("create", help="Create a new workspace")
    ws_create.add_argument("name")
    ws_edit = ws_sub.add_parser("edit", help="Edit workspace config in $EDITOR")
    ws_edit.add_argument("name")

    # plugin
    plugin_parser = subparsers.add_parser("plugin", help="Plugin management")
    plugin_sub = plugin_parser.add_subparsers(dest="plugin_command")
    plugin_sub.add_parser("list", help="List installed plugins")
    plugin_install = plugin_sub.add_parser("install", help="Install a plugin")
    plugin_install.add_argument("name")
    plugin_config = plugin_sub.add_parser("config", help="Configure a plugin (interactive)")
    plugin_config.add_argument("name")

    # status
    subparsers.add_parser("status", help="Check backend status")

    args = parser.parse_args()

    if args.command == "workspace":
        if args.ws_command == "list":
            cmd_workspace_list()
        elif args.ws_command == "create":
            cmd_workspace_create(args.name)
        elif args.ws_command == "edit":
            cmd_workspace_edit(args.name)
        else:
            ws_parser.print_help()
    elif args.command == "plugin":
        if args.plugin_command == "list":
            cmd_plugin_list()
        elif args.plugin_command == "install":
            print(f"Plugin install for '{args.name}' — full implementation in phase B8")
        elif args.plugin_command == "config":
            print(f"Plugin config for '{args.name}' — full implementation in phase B8")
        else:
            plugin_parser.print_help()
    elif args.command == "status":
        cmd_status()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
