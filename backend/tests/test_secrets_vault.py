"""Tests for the encrypted secrets vault."""
import os
import pytest


@pytest.fixture()
def vault(tmp_path):
    """Isolated vault for each test."""
    import sys, os
    # Run from tmp dir so secrets/ is created there
    orig = os.getcwd()
    os.chdir(tmp_path)
    from importlib import reload
    import core.secrets.crypto as crypto_mod
    import core.secrets.vault as vault_mod
    reload(crypto_mod)
    reload(vault_mod)
    from core.secrets.vault import SecretsVault
    v = SecretsVault(vault_path=str(tmp_path / "vault.enc"))
    yield v
    os.chdir(orig)


def test_set_and_get(vault):
    vault.set_secret("API_KEY", "supersecret123")
    assert vault.get_secret("API_KEY") == "supersecret123"


def test_preview_never_returns_full_value(vault):
    vault.set_secret("API_KEY", "supersecret123")
    preview = vault.get_preview("API_KEY")
    assert preview["is_set"] is True
    assert "supersecret123" not in preview["preview"]
    assert "****" in preview["preview"]


def test_preview_unset(vault):
    preview = vault.get_preview("NONEXISTENT")
    assert preview["is_set"] is False
    assert preview["preview"] == ""


def test_delete(vault):
    vault.set_secret("TO_DELETE", "value")
    vault.delete_secret("TO_DELETE")
    assert vault.get_secret("TO_DELETE") is None


def test_list_secrets(vault):
    vault.set_secret("KEY_A", "value_a")
    vault.set_secret("KEY_B", "value_b")
    names = vault.list_secrets()
    assert "KEY_A" in names
    assert "KEY_B" in names


def test_env_resolution(vault):
    vault.set_secret("TODOIST_TOKEN", "actual_value")
    env = vault.get_env_for_mcp({"TODOIST_API_TOKEN": "${TODOIST_TOKEN}", "STATIC": "static_val"})
    assert env["TODOIST_API_TOKEN"] == "actual_value"
    assert env["STATIC"] == "static_val"
