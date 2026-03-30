"""Pydantic request/response schemas shared across API modules."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ─── Enums ────────────────────────────────────────────────────────────────────

class ModeEnum(str, Enum):
    companion = "companion"
    meeting = "meeting"
    background = "background"
    autonomous = "autonomous"


class STTGateMode(str, Enum):
    always_on = "always_on"
    on_demand = "on_demand"
    smart = "smart"


class LLMProvider(str, Enum):
    ollama = "ollama"
    openai = "openai"
    anthropic = "anthropic"


class TTSEngine(str, Enum):
    kokoro = "kokoro"
    xtts = "xtts"
    piper = "piper"


class TranscriptionPriorityMode(str, Enum):
    smart = "smart"
    always_on = "always_on"
    off = "off"


# ─── System / Config ──────────────────────────────────────────────────────────

class SystemStatus(BaseModel):
    mode: ModeEnum
    workspace: str
    backend_version: str
    ollama_available: bool
    tts_loaded: bool
    stt_loaded: bool
    active_mcp_count: int


class AppConfig(BaseModel):
    llm_provider: LLMProvider = LLMProvider.ollama
    llm_model: str = "llama3.2"
    ollama_base_url: str = "http://localhost:11434"
    openai_model: str = "gpt-4o-mini"
    anthropic_model: str = "claude-sonnet-4-6"
    embed_model: str = "nomic-embed-text"
    whisper_model: str = "large-v3-turbo"
    whisper_device: str = "cuda"
    tts_engine: TTSEngine = TTSEngine.kokoro
    tts_voice: str = "af_heart"
    tts_language: str = "pt"
    default_mode: ModeEnum = ModeEnum.companion
    default_workspace: str = "personal"
    meeting_hotkey: str = "ctrl+shift+m"
    toggle_avatar_hotkey: str = "ctrl+shift+a"
    log_level: str = "INFO"


# ─── Modes ────────────────────────────────────────────────────────────────────

class ModeStatus(BaseModel):
    mode: ModeEnum
    workspace: str
    changed_at: datetime | None = None


class ModeSwitchRequest(BaseModel):
    mode: ModeEnum


# ─── Workspaces ───────────────────────────────────────────────────────────────

class TranscriptionPriorityConfig(BaseModel):
    mode: TranscriptionPriorityMode = TranscriptionPriorityMode.smart
    high_priority_topics: list[str] = Field(default_factory=list)
    low_priority_topics: list[str] = Field(default_factory=list)
    behavior: str = ""


class KnowledgeSeed(BaseModel):
    type: str  # "url" | "directory" | "notion_db"
    source: str


class WorkspaceConfig(BaseModel):
    name: str
    system_prompt: str = ""
    stt_gate_mode: STTGateMode = STTGateMode.smart
    proactivity: str = "medium"
    tools_enabled: list[str] = Field(default_factory=lambda: ["web_search"])
    plugins_enabled: list[str] = Field(default_factory=list)
    collections: list[str] = Field(default_factory=lambda: ["notes", "documents"])
    transcription_priority: TranscriptionPriorityConfig = Field(
        default_factory=TranscriptionPriorityConfig
    )
    knowledge_seeds: list[KnowledgeSeed] = Field(default_factory=list)


class WorkspaceCreate(BaseModel):
    name: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9\-]*$", min_length=1, max_length=64)
    system_prompt: str = ""
    template: str | None = None
    stt_gate_mode: STTGateMode = STTGateMode.smart


class WorkspaceSummary(BaseModel):
    name: str
    system_prompt_preview: str
    stt_gate_mode: STTGateMode
    plugins_enabled: list[str]
    created_at: str | None = None


# ─── Chats ────────────────────────────────────────────────────────────────────

class ChatRole(str, Enum):
    user = "user"
    assistant = "assistant"
    system = "system"


class ChatMessage(BaseModel):
    id: str
    role: ChatRole
    content: str
    timestamp: datetime
    workspace: str | None = None


class ChatSession(BaseModel):
    id: str
    workspace: str
    title: str | None = None
    created_at: datetime
    updated_at: datetime
    message_count: int


class ChatSearchRequest(BaseModel):
    query: str
    workspace: str | None = None
    session_id: str | None = None
    limit: int = 20


# ─── Knowledge ────────────────────────────────────────────────────────────────

class KnowledgeSource(BaseModel):
    id: str
    name: str
    type: str  # "file" | "url" | "directory" | "notion_db"
    workspace: str
    status: str  # "indexed" | "pending" | "error"
    indexed_at: datetime | None = None
    source_path: str


class KnowledgeSeedRequest(BaseModel):
    workspace: str
    type: str
    source: str


class KnowledgeSearchRequest(BaseModel):
    query: str
    workspace: str
    limit: int = 5


class KnowledgeSearchResult(BaseModel):
    content: str
    source: str
    score: float
    workspace: str


# ─── Plugins ──────────────────────────────────────────────────────────────────

class PluginStatus(str, Enum):
    running = "running"
    stopped = "stopped"
    error = "error"
    not_installed = "not_installed"


class PluginInfo(BaseModel):
    name: str
    version: str
    description: str
    type: str  # "mcp" | "builtin"
    tags: list[str] = Field(default_factory=list)
    required_env: list[str] = Field(default_factory=list)
    optional_env: list[str] = Field(default_factory=list)
    installed: bool = False
    status: PluginStatus = PluginStatus.not_installed
    workspaces_enabled: list[str] = Field(default_factory=list)


class PluginInstallRequest(BaseModel):
    name: str | None = None
    mcp_config: dict[str, Any] | None = None


class PluginEnableRequest(BaseModel):
    workspace: str


# ─── MCP Servers ──────────────────────────────────────────────────────────────

class McpServerStatus(str, Enum):
    running = "running"
    stopped = "stopped"
    error = "error"


class McpServerInfo(BaseModel):
    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    description: str = ""
    enabled: bool = True
    auto_start: bool = False
    status: McpServerStatus = McpServerStatus.stopped
    tools_count: int = 0
    uptime_seconds: float | None = None
    last_error: str | None = None


class McpServerCreate(BaseModel):
    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    description: str = ""
    auto_start: bool = False


class McpTool(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)


# ─── Secrets ──────────────────────────────────────────────────────────────────

class SecretSetRequest(BaseModel):
    name: str
    value: str


class SecretPreview(BaseModel):
    name: str
    is_set: bool
    preview: str  # e.g. "tdst_****7f2a" — never the full value


# ─── WebSocket ────────────────────────────────────────────────────────────────

class WsMessage(BaseModel):
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


# ─── Generic responses ────────────────────────────────────────────────────────

class OkResponse(BaseModel):
    ok: bool = True
    message: str = ""
