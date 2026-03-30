"""Encrypted secrets management endpoints.

SECURITY: This API never returns full secret values.
Only masked previews (e.g. "tdst_****7f2a") are returned.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter
from slowapi import Limiter
from slowapi.util import get_remote_address

from api.schemas import OkResponse, SecretPreview, SecretSetRequest

logger = logging.getLogger("api.secrets")
limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/api/secrets", tags=["secrets"])


def _get_vault():
    from core.secrets.vault import SecretsVault
    return SecretsVault()


@router.post("/set", response_model=OkResponse)
async def set_secret(request: SecretSetRequest) -> OkResponse:
    """Store or update an encrypted secret. Value is encrypted at rest immediately."""
    vault = _get_vault()
    vault.set_secret(request.name, request.value)
    logger.info(f"Secret '{request.name}' stored/updated.")
    return OkResponse(message=f"Secret '{request.name}' saved.")


@router.get("", response_model=list[SecretPreview])
async def list_secrets() -> list[SecretPreview]:
    """List all stored secret names with masked previews. Full values are never returned."""
    vault = _get_vault()
    secrets = vault.list_secrets()
    return [
        SecretPreview(**vault.get_preview(name))
        for name in secrets
    ]


@router.get("/{name}", response_model=SecretPreview)
async def get_secret_preview(name: str) -> SecretPreview:
    """Get masked preview of a secret. Full value is never returned."""
    vault = _get_vault()
    return SecretPreview(**vault.get_preview(name))


@router.delete("/{name}", response_model=OkResponse)
async def delete_secret(name: str) -> OkResponse:
    """Remove a secret from the vault."""
    vault = _get_vault()
    vault.delete_secret(name)
    logger.info(f"Secret '{name}' deleted.")
    return OkResponse(message=f"Secret '{name}' deleted.")
