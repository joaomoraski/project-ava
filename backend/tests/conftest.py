"""Pytest fixtures for the Ava Project backend tests."""
from __future__ import annotations

import json
import os
import shutil
import tempfile

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session", autouse=True)
def test_workspace(tmp_path_factory):
    """Create a temporary workspace directory for tests."""
    tmp = tmp_path_factory.mktemp("ava_test")
    orig_dir = os.getcwd()
    os.chdir(tmp)

    # Create required directories
    for d in [
        "workspaces/test/chroma_db",
        "workspaces/test/chat_history",
        "knowledge/meetings",
        "knowledge/documents",
        "knowledge/chroma_db",
        "secrets",
        "logs",
        "plugins",
        "mcp",
        "config",
    ]:
        os.makedirs(d, exist_ok=True)

    # Write default configs
    with open("mcp/servers.json", "w") as f:
        json.dump({"servers": {}}, f)
    with open("plugins/installed.json", "w") as f:
        json.dump({"plugins": {}}, f)
    with open("config/animations.json", "w") as f:
        json.dump({"animations": {}}, f)
    with open("workspaces/test/config.json", "w") as f:
        json.dump({
            "name": "test",
            "system_prompt": "",
            "stt_gate_mode": "smart",
            "proactivity": "medium",
            "tools_enabled": [],
            "plugins_enabled": [],
            "collections": [],
            "transcription_priority": {
                "mode": "smart",
                "high_priority_topics": [],
                "low_priority_topics": [],
                "behavior": "",
            },
            "knowledge_seeds": [],
        }, f)

    yield tmp

    os.chdir(orig_dir)


@pytest.fixture(scope="session")
def client(test_workspace):
    """Return a FastAPI TestClient with startup checks skipped."""
    import sys
    sys.path.insert(0, str(test_workspace.parent.parent / "backend"))

    # Patch startup checks so tests don't require Ollama
    import unittest.mock as mock
    with mock.patch("core.startup.run_startup_checks", return_value=None):
        from main import app
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c
