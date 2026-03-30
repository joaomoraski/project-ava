"""Ava Project — FastAPI backend entrypoint.

Run:
    uvicorn main:app --host 0.0.0.0 --port 8471 --reload
"""
from __future__ import annotations

import asyncio
import logging
import os

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting Ava Project API v{settings.app_version}")
    logger.info(f"API docs: http://localhost:{settings.api_port}/docs")
    logger.info(f"WebSocket: ws://localhost:{settings.ws_port}/ws")
    await run_startup_checks(settings)
    logger.info("Backend ready.")
    yield
    logger.info("Ava Project API shutting down.")


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


# ─── Lifecycle ───────────────────────────────────────────────────────────────

