"""Workspace management utilities — fully expanded in B6."""
from __future__ import annotations

import json
import os
import re
from typing import Any

WORKSPACES_DIR = "workspaces"
NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-]*$")


def validate_name(name: str) -> None:
    if not NAME_PATTERN.match(name) or len(name) > 64:
        raise ValueError(
            f"Invalid workspace name '{name}'. "
            "Use alphanumeric characters and hyphens (1-64 chars)."
        )
    reserved = {"api", "system", "admin", "config", "health"}
    if name.lower() in reserved:
        raise ValueError(f"'{name}' is a reserved name.")


def workspace_exists(name: str) -> bool:
    return os.path.exists(os.path.join(WORKSPACES_DIR, name, "config.json"))


def load_config(name: str) -> dict[str, Any]:
    config_path = os.path.join(WORKSPACES_DIR, name, "config.json")
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Workspace '{name}' not found.")
    with open(config_path) as f:
        return json.load(f)


def save_config(name: str, config: dict[str, Any]) -> None:
    config_path = os.path.join(WORKSPACES_DIR, name, "config.json")
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)


def list_workspaces() -> list[str]:
    if not os.path.exists(WORKSPACES_DIR):
        return []
    return [
        d for d in os.listdir(WORKSPACES_DIR)
        if os.path.isdir(os.path.join(WORKSPACES_DIR, d))
        and os.path.exists(os.path.join(WORKSPACES_DIR, d, "config.json"))
    ]


def create_workspace(name: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    validate_name(name)
    if workspace_exists(name):
        raise ValueError(f"Workspace '{name}' already exists.")
    os.makedirs(os.path.join(WORKSPACES_DIR, name, "chroma_db"), exist_ok=True)
    os.makedirs(os.path.join(WORKSPACES_DIR, name, "chat_history"), exist_ok=True)
    default_config: dict[str, Any] = {
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
    if config:
        default_config.update(config)
    save_config(name, default_config)
    return default_config
