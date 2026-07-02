"""Ava Project — FastAPI backend entrypoint.

Run:
    uvicorn main:app --host 0.0.0.0 --port 8471 --reload
"""
from __future__ import annotations

import logging

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware

from core.config import settings
from core.logging import setup_logging
from core.startup import run_startup_checks
from core.errors import http_exception_handler

# ─── Logging (must be first) ─────────────────────────────────────────────────
setup_logging(settings.log_level)
logger = logging.getLogger("main")

# ─── Rate limiter ────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])

# ─── Lifespan ────────────────────────────────────────────────────────────────

_tray = None

@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    global _tray

    logger.info(f"Starting Ava Project API v{settings.app_version}")
    logger.info(f"API docs: http://localhost:{settings.api_port}/docs")
    logger.info(f"WebSocket: ws://localhost:{settings.ws_port}/ws")

    # Database: run Alembic migrations and ensure defaults
    await _init_database()
    await run_startup_checks(settings)

    # --- Wire all backend modules together ---
    await _wire_components(fastapi_app)

    # Open procrastinate async connection (job deferral only — worker runs separately)
    from core.jobs.app import procrastinate_app
    _procrastinate_ctx = procrastinate_app.open_async()
    await _procrastinate_ctx.__aenter__()

    logger.info("Backend ready.")
    yield

    # --- Shutdown ---
    try:
        await _procrastinate_ctx.__aexit__(None, None, None)
    except Exception as _pe:
        logger.warning(f"Procrastinate close error (non-fatal): {_pe}")

    await _shutdown_components(fastapi_app)
    logger.info("Ava Project API shutting down.")


async def _init_database() -> None:
    """Run Alembic migrations and create default workspaces if needed."""
    import asyncio
    import sys

    # Run alembic in a subprocess — it needs its own event loop (asyncio.run)
    # and cannot share the running uvicorn loop.
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "alembic", "upgrade", "head",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Alembic migration failed:\n{stdout.decode()}")
    logger.info("Database migrations applied.")

    # Ensure default workspaces exist and have up-to-date tools
    from core.db.engine import async_session
    from core.workspace import workspace_exists, create_workspace, load_config, save_config, DEFAULT_CONFIG

    async with async_session() as session:
        for name in ("personal", "work"):
            if not await workspace_exists(session, name):
                await create_workspace(session, name)
                logger.info(f"Created default workspace: {name}")
            else:
                # Ensure existing workspaces have all default tools
                try:
                    config = await load_config(session, name)
                    current_tools = set(config.get("tools_enabled", []))
                    default_tools = set(DEFAULT_CONFIG["tools_enabled"])
                    missing = default_tools - current_tools
                    if missing:
                        merged = list(current_tools | default_tools)
                        await save_config(session, name, {"tools_enabled": merged})
                        logger.info(f"Updated workspace '{name}': added tools {missing}")

                    # Also ensure system_prompt is set if empty
                    if not config.get("system_prompt"):
                        await save_config(session, name, {"system_prompt": DEFAULT_CONFIG["system_prompt"]})
                        logger.info(f"Updated workspace '{name}': set default system_prompt")
                except Exception as e:
                    logger.warning(f"Could not update workspace '{name}': {e}")

    # Load persisted config overrides from database
    from core.config import load_persisted_config
    await load_persisted_config()

    # Load persisted state machine state
    from core.state_machine import state_machine
    await state_machine.load_persisted_state()


async def _wire_components(fastapi_app: FastAPI) -> None:
    """Wire all backend modules: plugins, voice pipeline, tray."""
    global _tray

    from api.ws import ws_manager
    from core.state_machine import state_machine

    # 1. Plugin registry + MCP
    try:
        from core.plugins.registry import plugin_registry, initialize_registry
        initialize_registry()
        logger.info("Plugin registry initialized.")
    except Exception as e:
        logger.warning(f"Plugin registry init failed (non-fatal): {e}")

    # 2. Voice components (graceful — each can fail independently)
    vad = None
    stt = None
    tts = None
    player = None

    try:
        from core.audio.vad import SileroVAD
        vad = SileroVAD()
        logger.info("SileroVAD loaded.")
    except Exception as e:
        logger.warning(f"VAD unavailable (voice pipeline will skip): {e}")

    try:
        from core.stt.whisper import WhisperSTT
        stt = WhisperSTT()
        stt.load()
        logger.info(f"WhisperSTT loaded (model={settings.whisper_model}, device={settings.whisper_device}).")
    except Exception as e:
        logger.warning(f"STT unavailable: {e}")

    # TTS — try configured engine, fall back to kokoro if it fails
    tts_engines_to_try = [settings.tts_engine]
    if settings.tts_engine != "kokoro":
        tts_engines_to_try.append("kokoro")  # fallback

    for engine_name in tts_engines_to_try:
        try:
            if engine_name == "kokoro":
                from core.tts.kokoro import KokoroTTS
                tts = KokoroTTS()
                tts.load()
            elif engine_name == "xtts":
                from core.tts.xtts import XTTSS
                tts = XTTSS()
                tts.load()
            elif engine_name == "piper":
                logger.info("Piper TTS not yet implemented — skipping.")
                continue
            if tts:
                logger.info(f"TTS engine ready: {engine_name}")
                break
        except Exception as e:
            logger.error(f"TTS engine '{engine_name}' failed: {e}", exc_info=True)
            tts = None
    if tts is None:
        logger.warning("No TTS engine available — voice output disabled.")

    try:
        from core.tts.playback import local_player
        player = local_player
    except Exception as e:
        logger.warning(f"Audio player unavailable: {e}")

    # 4. Build agent for default workspace
    agent = None
    try:
        from core.llm.agent import AvaAgent
        from core.plugins.registry import plugin_registry

        ws_config = await _get_workspace_config(settings.default_workspace)
        tools = plugin_registry.get_tools_for_workspace(ws_config)
        system_prompt = ws_config.get("system_prompt", "")
        agent = AvaAgent(tools=tools if tools else None, system_prompt=system_prompt or None)
        logger.info(f"AvaAgent initialized for workspace '{settings.default_workspace}' with {len(tools)} tools.")
    except Exception as e:
        logger.warning(f"Agent init failed: {e}")

    # Store globals so other endpoints can access them
    fastapi_app.state.agent = agent
    fastapi_app.state.ws_manager = ws_manager
    fastapi_app.state.stt = stt
    fastapi_app.state.tts = tts
    logger.info(f"Stored globals: stt={type(stt).__name__ if stt else 'None'}, tts={type(tts).__name__ if tts else 'None'}, agent={'yes' if agent else 'None'}")

    # 5. Voice pipeline (only if we have at least VAD + STT + agent)
    voice_pipeline = None
    if vad and stt and agent:
        try:
            from core.pipeline.voice_pipeline import VoicePipeline
            ws_config = await _get_workspace_config(settings.default_workspace)
            mic_dev = settings.mic_device if settings.mic_device else None
            voice_pipeline = VoicePipeline(
                vad=vad, stt=stt, agent=agent, tts=tts,
                player=player, ws_manager=ws_manager,
                workspace_config=ws_config,
                mic_device=mic_dev,
            )
            logger.info("Voice pipeline assembled.")
        except Exception as e:
            logger.warning(f"Voice pipeline assembly failed: {e}")
    else:
        missing = []
        if not vad: missing.append("VAD")
        if not stt: missing.append("STT")
        if not agent: missing.append("Agent")
        logger.info(f"Voice pipeline skipped (missing: {', '.join(missing)}). Text chat still available.")

    fastapi_app.state.voice_pipeline = voice_pipeline

    # 6. Inject components into state machine
    state_machine.set_components(
        vad=vad,
        tts_player=player,
        ws_manager=ws_manager,
    )
    if stt:
        state_machine.set_stt(stt)
    if voice_pipeline:
        state_machine.set_pipeline(voice_pipeline)
    if agent:
        state_machine.set_agent(agent)

    # Start voice pipeline if in companion mode
    if voice_pipeline and state_machine.mode.value == "companion":
        try:
            await voice_pipeline.start()
            logger.info("Voice pipeline started (companion mode).")
        except Exception as e:
            logger.warning(f"Voice pipeline start failed: {e}")

    # 7. Alerts background loop
    import asyncio
    asyncio.create_task(_alerts_loop())
    logger.info("Alerts background loop started.")

    # 7b. Calendar sync loop (every 5 minutes)
    asyncio.create_task(_calendar_sync_loop())
    logger.info("Calendar sync loop started.")

    # 8. System tray (background thread, non-blocking)
    loop = asyncio.get_running_loop()

    try:
        from core.tray import SystemTray
        _tray = SystemTray(event_loop=loop)
        _tray.start()
    except Exception as e:
        logger.warning(f"System tray unavailable: {e}")


async def _shutdown_components(fastapi_app: FastAPI) -> None:
    """Graceful shutdown of wired components."""
    global _tray

    if hasattr(fastapi_app.state, "voice_pipeline") and fastapi_app.state.voice_pipeline:
        try:
            await fastapi_app.state.voice_pipeline.stop()
        except Exception:
            pass

    if _tray:
        try:
            _tray.stop()
        except Exception:
            pass


async def _alerts_loop() -> None:
    """Background loop: check and fire due alerts every 60 seconds."""
    import asyncio
    while True:
        await asyncio.sleep(60)
        try:
            from api.alerts import check_and_fire_alerts
            from api.ws import ws_manager
            await check_and_fire_alerts(ws_manager)
        except Exception as e:
            logger.warning(f"Alerts check failed: {e}")


async def _calendar_sync_loop() -> None:
    """Background loop: enqueue a Google Calendar sync task every 5 minutes."""
    import asyncio
    while True:
        await asyncio.sleep(300)
        try:
            from core.db.engine import async_session
            from sqlalchemy import select
            from core.db.models import Workspace
            from core.jobs.tracker import create_job_task
            from core.jobs.tasks.calendar import sync_google_calendar
            # Sync for the default workspace; failures are non-fatal
            async with async_session() as session:
                result = await session.execute(
                    select(Workspace.id, Workspace.name).order_by(Workspace.created_at).limit(1)
                )
                row = result.first()
            if row:
                ws_id, ws_name = row
                async with async_session() as session:
                    task_row = await create_job_task(
                        session=session,
                        job_type="sync_google_calendar",
                        target_type=None,
                        target_id=None,
                        workspace_id=ws_id,
                    )
                await sync_google_calendar.defer_async(
                    workspace=ws_name,
                    task_id=str(task_row.id),
                )
        except Exception as e:
            logger.debug(f"Calendar sync enqueue failed (non-fatal): {e}")


async def _get_workspace_config(workspace_name: str) -> dict:
    """Load workspace config from the database."""
    from core.db.engine import async_session
    from core.workspace import load_config
    try:
        async with async_session() as session:
            return await load_config(session, workspace_name)
    except Exception:
        return {}


# ─── App ─────────────────────────────────────────────────────────────────────
app = FastAPI(
    lifespan=lifespan,
    title="Ava Project API",
    description=(
        "Local AI assistant backend. "
        "Voice pipeline, LLM agent, knowledge base, plugins, and more."
    ),
    version=settings.app_version,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# ─── Middleware ───────────────────────────────────────────────────────────────

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Ava-Version"] = settings.app_version
        return response


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"http://localhost:{settings.frontend_port}",
        f"http://127.0.0.1:{settings.frontend_port}",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_exception_handler(HTTPException, http_exception_handler)

# ─── Routers ──────────────────────────────────────────────────────────────────
from api.ws import router as ws_router
from api.auth import router as auth_router
from api.config_api import router as config_router
from api.modes import router as modes_router
from api.workspaces import router as workspaces_router
from api.chats import router as chats_router
from api.knowledge import router as knowledge_router
from api.plugins import router as plugins_router
from api.mcp import router as mcp_router
from api.secrets import router as secrets_router
from api.voice import router as voice_router
from api.meetings import router as meetings_router
from api.action_items import router as action_items_router
from api.notes import router as notes_router
from api.todos import router as todos_router
from api.alerts import router as alerts_router
from api.calendar import router as calendar_router
from api.transfer import router as transfer_router
from api.contexts import router as contexts_router
from api.jobs import router as jobs_router

app.include_router(ws_router)
app.include_router(auth_router)
app.include_router(config_router)
app.include_router(modes_router)
app.include_router(workspaces_router)
app.include_router(chats_router)
app.include_router(knowledge_router)
app.include_router(plugins_router)
app.include_router(mcp_router)
app.include_router(secrets_router)
app.include_router(voice_router)
app.include_router(meetings_router)
app.include_router(action_items_router)
app.include_router(notes_router)
app.include_router(todos_router)
app.include_router(alerts_router)
app.include_router(calendar_router)
app.include_router(transfer_router)
app.include_router(contexts_router)
app.include_router(jobs_router)


# ─── Root ────────────────────────────────────────────────────────────────────

@app.get("/", tags=["health"])
async def root() -> dict:
    return {
        "name": "Ava Project API",
        "version": settings.app_version,
        "docs": "/docs",
        "status": "running",
    }


@app.get("/health", tags=["health"])
async def health() -> dict:
    return {"status": "ok", "version": settings.app_version}
