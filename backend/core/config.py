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
    llm_provider: Literal["ollama", "openai", "anthropic"] = "ollama"
    llm_model: str = "llama3.2"
    ollama_base_url: str = "http://localhost:11434"

    # External APIs
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    # Embeddings
    embed_model: str = "nomic-embed-text"

    # STT
    whisper_model: str = "large-v3-turbo"
    whisper_device: str = "cuda"

    # TTS
    tts_engine: Literal["kokoro", "xtts", "piper"] = "kokoro"
    tts_voice: str = "af_heart"
    tts_language: str = "pt"
    xtts_voice_sample: str = "voices/character.wav"

    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""

    # HuggingFace
    hf_token: str = ""

    # Modes
    default_mode: Literal["companion", "background", "autonomous"] = "companion"
    default_workspace: str = "personal"
    meeting_hotkey: str = "ctrl+shift+m"
    toggle_avatar_hotkey: str = "ctrl+shift+a"
    toggle_workspace_hotkey: str = "ctrl+shift+w"

    # Ports
    api_port: int = 8471
    ws_port: int = 8472

    # Logging
    log_level: str = "INFO"

    # Frontend
    frontend_port: int = 4731

    # Version (used for X-Ava-Version header)
    app_version: str = "0.1.0"


# Singleton
settings = Settings()
