"""Central application configuration via pydantic-settings."""
import os
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM
    llm_provider: Literal["ollama", "openai", "anthropic", "google"] = "ollama"
    llm_model: str = "llama3.2"
    ollama_base_url: str = "http://localhost:11434"

    # External APIs
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    google_api_key: str = ""
    google_model: str = "gemini-2.0-flash"

    # Embeddings
    embed_model: str = "nomic-embed-text"

    # Audio input
    mic_device: str = ""  # sounddevice device name or index, empty = system default

    # STT
    whisper_model: str = "large-v3-turbo"
    whisper_device: str = "cuda"

    # TTS
    tts_engine: Literal["kokoro", "xtts", "piper"] = "kokoro"
    tts_voice: str = "af_heart"
    tts_language: str = "pt"
    xtts_voice_sample: str = "voices/character.wav"

    # Tavily (web search)
    tavily_api_key: str = ""

    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""

    # HuggingFace
    hf_token: str = ""

    # Modes
    default_mode: Literal["companion", "meeting", "background"] = "companion"
    default_workspace: str = "personal"

    # Database
    database_url: str = "postgresql+asyncpg://ava:ava_dev_password@localhost:5432/ava"

    # Ports
    api_port: int = 8471
    ws_port: int = 8472

    # Logging
    log_level: str = "INFO"

    # Frontend
    frontend_port: int = 4731

    # Context window management
    max_context_tokens: int = 6000

    # Version (used for X-Ava-Version header)
    app_version: str = "0.1.0"


# Singleton
settings = Settings()


async def load_persisted_config() -> None:
    """Load config overrides from the AppState PostgreSQL table into settings."""
    import json
    import logging

    logger = logging.getLogger("core.config")

    try:
        from core.db.engine import async_session
        from core.db.models import AppState
        from sqlalchemy import select

        async with async_session() as session:
            result = await session.execute(select(AppState))
            rows = result.scalars().all()

        for row in rows:
            if hasattr(settings, row.key):
                try:
                    value = json.loads(row.value)
                    setattr(settings, row.key, value)
                except Exception as e:
                    logger.warning(f"Could not restore config key '{row.key}': {e}")

        if rows:
            logger.info(f"Loaded {len(rows)} persisted config value(s) from database.")
    except Exception as e:
        logger.warning(f"Could not load persisted config (non-fatal): {e}")
