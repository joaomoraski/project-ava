"""Preflight startup checks."""
import json
import os
import shutil
import socket
import logging

import httpx

from core.config import Settings

logger = logging.getLogger("core.startup")


def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) == 0


def ensure_directories(paths: list[str]) -> None:
    for path in paths:
        os.makedirs(path, exist_ok=True)

    # secrets/ with tight permissions
    secrets_dir = "secrets"
    if not os.path.exists(secrets_dir):
        os.makedirs(secrets_dir, mode=0o700, exist_ok=True)
    else:
        os.chmod(secrets_dir, 0o700)


def write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _create_default_workspace_config(workspace_name: str) -> None:
    config_path = f"workspaces/{workspace_name}/config.json"
    if not os.path.exists(config_path):
        write_json(
            config_path,
            {
                "name": workspace_name,
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
            },
        )


async def run_startup_checks(config: Settings) -> None:
    """Run all preflight checks. Logs warnings, raises SystemExit on critical failures."""

    # 1. Ensure directory structure
    ensure_directories(
        [
            "workspaces/personal/chroma_db",
            "workspaces/personal/chat_history",
            "workspaces/work/chroma_db",
            "workspaces/work/chat_history",
            "knowledge/meetings",
            "knowledge/documents",
            "knowledge/chroma_db",
            "logs",
            "plugins",
            "mcp",
            "voices",
            "config",
        ]
    )
    _create_default_workspace_config("personal")
    _create_default_workspace_config("work")

    # 2. Create default MCP servers config
    if not os.path.exists("mcp/servers.json"):
        write_json("mcp/servers.json", {"servers": {}})

    # 3. Create default installed plugins manifest
    if not os.path.exists("plugins/installed.json"):
        write_json("plugins/installed.json", {"plugins": {}})

    # 4. Create default animations config
    if not os.path.exists("config/animations.json"):
        write_json(
            "config/animations.json",
            {
                "animations": {
                    "idle": {"file": None, "trigger_intents": []},
                    "wave": {
                        "file": None,
                        "trigger_intents": ["greet", "hello", "wave"],
                    },
                    "nod": {
                        "file": None,
                        "trigger_intents": ["agree", "yes", "confirm"],
                    },
                    "dance": {
                        "file": None,
                        "trigger_intents": ["dance", "celebrate", "party"],
                    },
                    "think": {
                        "file": None,
                        "trigger_intents": ["think", "consider", "hmm"],
                    },
                }
            },
        )

    # 5. Check port availability
    for name, port in [("API", config.api_port), ("WebSocket", config.ws_port)]:
        if is_port_in_use(port):
            logger.error(
                f"Port {port} ({name}) is already in use. "
                f"Change {name.upper().replace(' ', '_')}_PORT in .env or stop the other process."
            )
            raise SystemExit(1)

    # 6. Check Ollama (if configured)
    if config.llm_provider == "ollama":
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{config.ollama_base_url}/api/tags")
                resp.raise_for_status()
                models = [m["name"] for m in resp.json().get("models", [])]

                def _model_present(name: str) -> bool:
                    # Ollama may return "llama3.2:latest" for "llama3.2"
                    return any(m.split(":")[0] == name.split(":")[0] for m in models)

                if not _model_present(config.llm_model):
                    logger.warning(
                        f"LLM model '{config.llm_model}' not found in Ollama. "
                        f"Run: ollama pull {config.llm_model}"
                    )
                if not _model_present(config.embed_model):
                    logger.warning(
                        f"Embedding model '{config.embed_model}' not found in Ollama. "
                        f"Run: ollama pull {config.embed_model}"
                    )
        except (httpx.ConnectError, httpx.TimeoutException):
            logger.error(
                "Ollama is not running. Install from https://ollama.com/download "
                "then run: ollama serve"
            )
            raise SystemExit(1)

    # 7. Check disk space
    free_gb = shutil.disk_usage("/").free / (1024**3)
    if free_gb < 30:
        logger.warning(
            f"Low disk space: {free_gb:.1f} GB free. Models require ~20 GB+."
        )

    # 8. Check CUDA (if whisper_device=cuda)
    if config.whisper_device == "cuda":
        try:
            import torch  # type: ignore

            if not torch.cuda.is_available():
                logger.warning(
                    "WHISPER_DEVICE=cuda but CUDA not available. "
                    "Set WHISPER_DEVICE=cpu in .env."
                )
        except ImportError:
            logger.warning(
                "PyTorch not installed. STT will not work without it. "
                "Set WHISPER_DEVICE=cpu or install torch."
            )

    # 9. HuggingFace token — warn only
    if not config.hf_token:
        logger.info(
            "HF_TOKEN not set. Meeting mode speaker diarization will be unavailable. "
            "See .env.example for instructions."
        )

    logger.info("Startup checks complete.")
