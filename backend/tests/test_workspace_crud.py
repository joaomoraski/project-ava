"""Tests for workspace CLI commands."""
import json
import os
import re


def test_workspace_list(test_workspace):
    from cli import cmd_workspace_list
    cmd_workspace_list()  # just ensure it doesn't raise


def test_workspace_create(test_workspace):
    from cli import cmd_workspace_create
    cmd_workspace_create("my-workspace")
    config_path = "workspaces/my-workspace/config.json"
    assert os.path.exists(config_path)
    with open(config_path) as f:
        cfg = json.load(f)
    assert cfg["name"] == "my-workspace"


def test_workspace_create_invalid_name(test_workspace):
    from cli import cmd_workspace_create
    import sys
    with pytest.raises(SystemExit):
        cmd_workspace_create("invalid name!")


def test_workspace_create_duplicate(test_workspace):
    from cli import cmd_workspace_create
    import sys
    cmd_workspace_create("unique-ws")
    with pytest.raises(SystemExit):
        cmd_workspace_create("unique-ws")


import pytest
