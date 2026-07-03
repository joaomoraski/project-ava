"""Ava CLI — workspace and plugin management.

Usage:
    python cli.py workspace list
    python cli.py workspace create <name>
    python cli.py workspace edit <name>
    python cli.py workspace show <name>
    python cli.py workspace delete <name>
    python cli.py workspace stt-gate <name> <mode>
    python cli.py knowledge seed --workspace <name> --url <url>
    python cli.py knowledge seed --workspace <name> --dir <path>
    python cli.py plugin list
    python cli.py plugin install <name>
    python cli.py plugin enable <name> --workspace <ws>
    python cli.py plugin disable <name> --workspace <ws>
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


def cmd_workspace_create(name: str, system_prompt: str = "", stt_gate_mode: str = "smart") -> None:
    import re
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9\-]*$", name) or len(name) > 64:
        print(f"Error: workspace name '{name}' is invalid. Use alphanumeric characters and hyphens only.")
        sys.exit(1)

    reserved = {"api", "system", "admin", "config", "health"}
    if name.lower() in reserved:
        print(f"Error: '{name}' is a reserved name.")
        sys.exit(1)

    ws_path = f"workspaces/{name}"
    config_path = f"{ws_path}/config.json"

    if os.path.exists(config_path):
        print(f"Error: workspace '{name}' already exists.")
        sys.exit(1)

    valid_modes = {"smart", "always_on", "on_demand"}
    if stt_gate_mode not in valid_modes:
        print(f"Error: stt-gate must be one of: {', '.join(sorted(valid_modes))}")
        sys.exit(1)

    os.makedirs(f"{ws_path}/chroma_db", exist_ok=True)
    os.makedirs(f"{ws_path}/chat_history", exist_ok=True)

    config = {
        "name": name,
        "system_prompt": system_prompt,
        "stt_gate_mode": stt_gate_mode,
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


def cmd_workspace_show(name: str) -> None:
    config_path = f"workspaces/{name}/config.json"
    if not os.path.exists(config_path):
        print(f"Error: workspace '{name}' not found.")
        sys.exit(1)
    with open(config_path) as f:
        cfg = json.load(f)
    print(json.dumps(cfg, indent=2, ensure_ascii=False))


def cmd_workspace_edit(name: str) -> None:
    config_path = f"workspaces/{name}/config.json"
    if not os.path.exists(config_path):
        print(f"Error: workspace '{name}' not found.")
        sys.exit(1)
    editor = os.environ.get("EDITOR", "nano")
    os.execvp(editor, [editor, config_path])


def cmd_workspace_delete(name: str, force: bool = False) -> None:
    import shutil
    config_path = f"workspaces/{name}/config.json"
    if not os.path.exists(config_path):
        print(f"Error: workspace '{name}' not found.")
        sys.exit(1)
    if name == "personal":
        print("Error: cannot delete the default 'personal' workspace.")
        sys.exit(1)
    if not force:
        confirm = input(f"Delete workspace '{name}' and all its data? Type the workspace name to confirm: ")
        if confirm.strip() != name:
            print("Aborted.")
            sys.exit(0)
    shutil.rmtree(f"workspaces/{name}")
    print(f"Workspace '{name}' deleted.")


def cmd_workspace_stt_gate(name: str, mode: str) -> None:
    config_path = f"workspaces/{name}/config.json"
    if not os.path.exists(config_path):
        print(f"Error: workspace '{name}' not found.")
        sys.exit(1)
    valid = {"smart", "always_on", "on_demand"}
    if mode not in valid:
        print(f"Error: mode must be one of: {', '.join(sorted(valid))}")
        sys.exit(1)
    with open(config_path) as f:
        cfg = json.load(f)
    cfg["stt_gate_mode"] = mode
    with open(config_path, "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"STT gate for '{name}' set to: {mode}")


def cmd_knowledge_seed(workspace: str, url: str | None = None, directory: str | None = None) -> None:
    """Add a knowledge seed to a workspace (URL or directory)."""
    config_path = f"workspaces/{workspace}/config.json"
    if not os.path.exists(config_path):
        print(f"Error: workspace '{workspace}' not found.")
        sys.exit(1)

    if not url and not directory:
        print("Error: provide --url or --dir")
        sys.exit(1)

    with open(config_path) as f:
        cfg = json.load(f)

    seeds = cfg.get("knowledge_seeds", [])
    if url:
        seed = {"type": "url", "source": url}
        seeds.append(seed)
        print(f"Added URL seed to '{workspace}': {url}")
    elif directory:
        seed = {"type": "directory", "source": os.path.abspath(directory)}
        seeds.append(seed)
        print(f"Added directory seed to '{workspace}': {directory}")

    cfg["knowledge_seeds"] = seeds
    with open(config_path, "w") as f:
        json.dump(cfg, f, indent=2)
    print("Seed saved. Run indexing to process it.")


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


def cmd_plugin_enable(plugin_name: str, workspace: str) -> None:
    config_path = f"workspaces/{workspace}/config.json"
    if not os.path.exists(config_path):
        print(f"Error: workspace '{workspace}' not found.")
        sys.exit(1)
    with open(config_path) as f:
        cfg = json.load(f)
    enabled = cfg.get("plugins_enabled", [])
    if plugin_name not in enabled:
        enabled.append(plugin_name)
        cfg["plugins_enabled"] = enabled
        with open(config_path, "w") as f:
            json.dump(cfg, f, indent=2)
        print(f"Plugin '{plugin_name}' enabled for workspace '{workspace}'.")
    else:
        print(f"Plugin '{plugin_name}' is already enabled in '{workspace}'.")


def cmd_plugin_disable(plugin_name: str, workspace: str) -> None:
    config_path = f"workspaces/{workspace}/config.json"
    if not os.path.exists(config_path):
        print(f"Error: workspace '{workspace}' not found.")
        sys.exit(1)
    with open(config_path) as f:
        cfg = json.load(f)
    enabled = cfg.get("plugins_enabled", [])
    if plugin_name in enabled:
        enabled.remove(plugin_name)
        cfg["plugins_enabled"] = enabled
        with open(config_path, "w") as f:
            json.dump(cfg, f, indent=2)
        print(f"Plugin '{plugin_name}' disabled for workspace '{workspace}'.")
    else:
        print(f"Plugin '{plugin_name}' is not enabled in '{workspace}'.")


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
    ws_create.add_argument("--system-prompt", default="")
    ws_create.add_argument("--stt-gate", default="smart", choices=["smart", "always_on", "on_demand"])

    ws_show = ws_sub.add_parser("show", help="Show workspace config as JSON")
    ws_show.add_argument("name")

    ws_edit = ws_sub.add_parser("edit", help="Edit workspace config in $EDITOR")
    ws_edit.add_argument("name")

    ws_delete = ws_sub.add_parser("delete", help="Delete workspace and all its data")
    ws_delete.add_argument("name")
    ws_delete.add_argument("--force", action="store_true", help="Skip confirmation")

    ws_gate = ws_sub.add_parser("stt-gate", help="Set STT gate mode for workspace")
    ws_gate.add_argument("name")
    ws_gate.add_argument("mode", choices=["smart", "always_on", "on_demand"])

    # knowledge
    know_parser = subparsers.add_parser("knowledge", help="Knowledge base management")
    know_sub = know_parser.add_subparsers(dest="know_command")
    know_seed = know_sub.add_parser("seed", help="Add knowledge seed to workspace")
    know_seed.add_argument("--workspace", required=True)
    know_seed.add_argument("--url", default=None)
    know_seed.add_argument("--dir", default=None, dest="directory")

    # plugin
    plugin_parser = subparsers.add_parser("plugin", help="Plugin management")
    plugin_sub = plugin_parser.add_subparsers(dest="plugin_command")
    plugin_sub.add_parser("list", help="List installed plugins")

    plugin_install = plugin_sub.add_parser("install", help="Install a plugin")
    plugin_install.add_argument("name")

    plugin_enable = plugin_sub.add_parser("enable", help="Enable plugin for workspace")
    plugin_enable.add_argument("name")
    plugin_enable.add_argument("--workspace", required=True)

    plugin_disable = plugin_sub.add_parser("disable", help="Disable plugin for workspace")
    plugin_disable.add_argument("name")
    plugin_disable.add_argument("--workspace", required=True)

    # status
    subparsers.add_parser("status", help="Check backend status")

    args = parser.parse_args()

    if args.command == "workspace":
        if args.ws_command == "list":
            cmd_workspace_list()
        elif args.ws_command == "create":
            cmd_workspace_create(
                args.name,
                system_prompt=args.system_prompt,
                stt_gate_mode=args.stt_gate,
            )
        elif args.ws_command == "show":
            cmd_workspace_show(args.name)
        elif args.ws_command == "edit":
            cmd_workspace_edit(args.name)
        elif args.ws_command == "delete":
            cmd_workspace_delete(args.name, force=args.force)
        elif args.ws_command == "stt-gate":
            cmd_workspace_stt_gate(args.name, args.mode)
        else:
            ws_parser.print_help()
    elif args.command == "knowledge":
        if args.know_command == "seed":
            cmd_knowledge_seed(
                workspace=args.workspace,
                url=args.url,
                directory=args.directory,
            )
        else:
            know_parser.print_help()
    elif args.command == "plugin":
        if args.plugin_command == "list":
            cmd_plugin_list()
        elif args.plugin_command == "install":
            print(f"Plugin install for '{args.name}' — full implementation in phase B8")
        elif args.plugin_command == "enable":
            cmd_plugin_enable(args.name, workspace=args.workspace)
        elif args.plugin_command == "disable":
            cmd_plugin_disable(args.name, workspace=args.workspace)
        else:
            plugin_parser.print_help()
    elif args.command == "status":
        cmd_status()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
