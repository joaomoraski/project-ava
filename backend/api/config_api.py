"""Application config endpoints."""
from __future__ import annotations

import logging

from fastapi import APIRouter
from slowapi import Limiter
from slowapi.util import get_remote_address

from api.schemas import AppConfig, OkResponse
from core.config import settings

logger = logging.getLogger("api.config")
limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("", response_model=AppConfig)
async def get_config() -> AppConfig:
    """Return current application configuration."""
    return AppConfig(
        llm_provider=settings.llm_provider,  # type: ignore[arg-type]
        llm_model=settings.llm_model,
        ollama_base_url=settings.ollama_base_url,
        openai_model=settings.openai_model,
        anthropic_model=settings.anthropic_model,
        embed_model=settings.embed_model,
        whisper_model=settings.whisper_model,
        whisper_device=settings.whisper_device,
        tts_engine=settings.tts_engine,  # type: ignore[arg-type]
        tts_voice=settings.tts_voice,
        tts_language=settings.tts_language,
        default_mode=settings.default_mode,  # type: ignore[arg-type]
        default_workspace=settings.default_workspace,
        meeting_hotkey=settings.meeting_hotkey,
        toggle_avatar_hotkey=settings.toggle_avatar_hotkey,
        log_level=settings.log_level,
    )


@router.put("", response_model=OkResponse)
async def update_config(payload: AppConfig) -> OkResponse:
    """
    Update runtime configuration.
    Note: Changes affecting LLM/STT/TTS require restart to take full effect.
    For persistent changes, update .env directly.
    """
    # Update in-memory settings (runtime only)
    for field, value in payload.model_dump(exclude_none=True).items():
        if hasattr(settings, field):
            try:
                setattr(settings, field, value)
            except Exception:
                pass  # Some fields are frozen
    logger.info("Config updated at runtime.")
    return OkResponse(message="Config updated. Restart required for STT/TTS/LLM changes.")
