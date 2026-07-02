"""Encrypted secrets management endpoints.

SECURITY: This API never returns full secret values.
Only masked previews (e.g. "tdst_****7f2a") are returned.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import OkResponse, SecretPreview, SecretSetRequest
from core.db.engine import get_session
from core.secrets.vault import SecretsVault

logger = logging.getLogger("api.secrets")
limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/api/secrets", tags=["secrets"])

_vault = SecretsVault()


@router.post("/set", response_model=OkResponse)
async def set_secret(
    request: SecretSetRequest,
    session: AsyncSession = Depends(get_session),
) -> OkResponse:
    await _vault.set_secret(session, request.name, request.value)
    logger.info(f"Secret '{request.name}' stored/updated.")
    return OkResponse(message=f"Secret '{request.name}' saved.")


@router.get("", response_model=list[SecretPreview])
async def list_secrets(session: AsyncSession = Depends(get_session)) -> list[SecretPreview]:
    secrets = await _vault.list_secrets(session)
    results = []
    for name in secrets:
        preview = await _vault.get_preview(session, name)
        results.append(SecretPreview(**preview))
    return results


@router.get("/{name}", response_model=SecretPreview)
async def get_secret_preview(
    name: str,
    session: AsyncSession = Depends(get_session),
) -> SecretPreview:
    preview = await _vault.get_preview(session, name)
    return SecretPreview(**preview)


@router.delete("/{name}", response_model=OkResponse)
async def delete_secret(
    name: str,
    session: AsyncSession = Depends(get_session),
) -> OkResponse:
    await _vault.delete_secret(session, name)
    logger.info(f"Secret '{name}' deleted.")
    return OkResponse(message=f"Secret '{name}' deleted.")
