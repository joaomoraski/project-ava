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
    log_level: str = "INFO"


# ─── Modes ────────────────────────────────────────────────────────────────────

class ModeStatus(BaseModel):
    mode: ModeEnum
    workspace: str
    changed_at: datetime | None = None
    meeting_id: str | None = None
    pre_meeting_mode: str | None = None


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
    error: str | None = None
    last_error: str | None = None  # alias kept for backward compat
    # OAuth metadata (presence indicates OAuth is configured for this server)
    oauth_url: str | None = None
    oauth_client_id: str | None = None
    oauth_scopes: str | None = None
    oauth_client_secret_ref: str | None = None


class McpServerCreate(BaseModel):
    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    description: str = ""
    auto_start: bool = False
    # OAuth fields — only needed for servers that require OAuth authorization
    oauth_url: str | None = None
    oauth_client_id: str | None = None
    oauth_scopes: str | None = None
    oauth_client_secret_ref: str | None = None  # name of secret in vault


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


# ─── Meetings ─────────────────────────────────────────────────────────────────

class MeetingCreate(BaseModel):
    title: str = ""
    workspace: str = "personal"
    participants: list[str] = []
    calendar_event_id: str | None = None


class MeetingUpdate(BaseModel):
    title: str | None = None
    participants: list[str] | None = None
    summary: str | None = None
    status: str | None = None


class MeetingResponse(BaseModel):
    id: str
    workspace_id: str
    title: str
    participants: list[str]
    transcript: str
    summary: str | None
    decisions: list[dict]
    document_md: str | None
    status: str
    started_at: str
    ended_at: str | None


class MeetingPrepResponse(BaseModel):
    past_meetings: list[dict]
    open_action_items: list[dict]


# ─── Action Items ─────────────────────────────────────────────────────────────

class ActionItemCreate(BaseModel):
    description: str
    workspace: str = "personal"
    meeting_id: str | None = None
    owner: str | None = None
    due_date: str | None = None


class ActionItemUpdate(BaseModel):
    description: str | None = None
    owner: str | None = None
    status: str | None = None
    due_date: str | None = None


class ActionItemResponse(BaseModel):
    id: str
    meeting_id: str | None
    workspace_id: str
    owner: str | None
    description: str
    status: str
    due_date: str | None
    completed_at: str | None
    linked_item_id: str | None


# ─── Notes ────────────────────────────────────────────────────────────────────

class NoteCreate(BaseModel):
    title: str
    content: str = ""
    workspace: str = "personal"
    tags: list[str] = []


class NoteUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    tags: list[str] | None = None


class NoteResponse(BaseModel):
    id: str
    workspace_id: str
    title: str
    content: str
    tags: list[str]
    created_at: str
    updated_at: str


# ─── Todos ────────────────────────────────────────────────────────────────────

class TodoCreate(BaseModel):
    title: str
    description: str = ""
    priority: str = "medium"
    due_date: str | None = None
    workspace: str = "personal"


class TodoUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    priority: str | None = None
    status: str | None = None
    due_date: str | None = None


class TodoResponse(BaseModel):
    id: str
    workspace_id: str | None
    title: str
    description: str
    priority: str
    status: str
    due_date: str | None
    completed_at: str | None
    created_at: str
    updated_at: str


# ─── Alerts ───────────────────────────────────────────────────────────────────

class AlertCreate(BaseModel):
    title: str
    message: str = ""
    trigger_at: str  # ISO 8601
    repeat_rule: str = "once"
    workspace: str = "personal"


class AlertUpdate(BaseModel):
    title: str | None = None
    message: str | None = None
    trigger_at: str | None = None
    repeat_rule: str | None = None
    status: str | None = None


class AlertResponse(BaseModel):
    id: str
    workspace_id: str | None
    title: str
    message: str
    trigger_at: str
    repeat_rule: str
    status: str
    fired_at: str | None
    created_at: str


# ─── Contexts ─────────────────────────────────────────────────────────────────

class ContextCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    color: str = "#6366f1"
    workspace: str = "personal"


class ContextUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    description: str | None = None
    color: str | None = None


class ContextResponse(BaseModel):
    id: str
    workspace_id: str
    name: str
    description: str
    color: str
    created_at: str


class ContextLinkRequest(BaseModel):
    item_type: str  # meeting, note, todo, alert, action_item
    item_id: str


class ContextLinkResponse(BaseModel):
    id: str
    context_id: str
    item_type: str
    item_id: str
    auto_linked: bool = False
    created_at: str


# ─── Jobs ─────────────────────────────────────────────────────────────────────

class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class JobTaskResponse(BaseModel):
    id: str
    job_type: str
    target_type: str | None = None
    target_id: str | None = None
    workspace_id: str
    status: JobStatus
    progress: float
    progress_message: str | None = None
    procrastinate_job_id: int | None = None
    error: str | None = None
    result_payload: dict[str, Any] | None = None
    created_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


class JobsListResponse(BaseModel):
    jobs: list[JobTaskResponse]
    count: int


# ─── Generic responses ────────────────────────────────────────────────────────

class OkResponse(BaseModel):
    ok: bool = True
    message: str = ""
