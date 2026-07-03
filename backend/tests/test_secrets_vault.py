"""Tests for the encrypted secrets vault — PostgreSQL backend."""
import pytest

from core.secrets.vault import SecretsVault


@pytest.fixture()
def vault():
    return SecretsVault()


@pytest.mark.asyncio
async def test_set_and_get(vault, db_session):
    await vault.set_secret(db_session, "TEST_KEY_1", "supersecret123")
    assert await vault.get_secret(db_session, "TEST_KEY_1") == "supersecret123"


@pytest.mark.asyncio
async def test_preview_never_returns_full_value(vault, db_session):
    await vault.set_secret(db_session, "TEST_KEY_2", "supersecret123")
    preview = await vault.get_preview(db_session, "TEST_KEY_2")
    assert preview["is_set"] is True
    assert "supersecret123" not in preview["preview"]
    assert "****" in preview["preview"]


@pytest.mark.asyncio
async def test_preview_unset(vault, db_session):
    preview = await vault.get_preview(db_session, "NONEXISTENT_XYZ")
    assert preview["is_set"] is False
    assert preview["preview"] == ""


@pytest.mark.asyncio
async def test_delete(vault, db_session):
    await vault.set_secret(db_session, "TEST_TO_DELETE", "value")
    await vault.delete_secret(db_session, "TEST_TO_DELETE")
    assert await vault.get_secret(db_session, "TEST_TO_DELETE") is None


@pytest.mark.asyncio
async def test_list_secrets(vault, db_session):
    await vault.set_secret(db_session, "TEST_KEY_A", "value_a")
    await vault.set_secret(db_session, "TEST_KEY_B", "value_b")
    names = await vault.list_secrets(db_session)
    assert "TEST_KEY_A" in names
    assert "TEST_KEY_B" in names


@pytest.mark.asyncio
async def test_env_resolution(vault, db_session):
    await vault.set_secret(db_session, "TODOIST_TOKEN", "actual_value")
    env = await vault.get_env_for_mcp(
        db_session,
        {"TODOIST_API_TOKEN": "${TODOIST_TOKEN}", "STATIC": "static_val"},
    )
    assert env["TODOIST_API_TOKEN"] == "actual_value"
    assert env["STATIC"] == "static_val"
