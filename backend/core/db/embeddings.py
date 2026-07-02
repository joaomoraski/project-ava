"""Embedding generation via Ollama API — used for pgvector inserts and queries."""
from __future__ import annotations

import logging

import httpx

from core.config import settings

logger = logging.getLogger("core.db.embeddings")


async def generate_embedding(text: str) -> list[float]:
    """Generate embedding vector from text using Ollama."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{settings.ollama_base_url}/api/embeddings",
            json={"model": settings.embed_model, "prompt": text},
        )
        resp.raise_for_status()
        return resp.json()["embedding"]


async def generate_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """Generate embeddings for multiple texts. Sequential calls to Ollama."""
    results = []
    async with httpx.AsyncClient(timeout=60.0) as client:
        for text in texts:
            resp = await client.post(
                f"{settings.ollama_base_url}/api/embeddings",
                json={"model": settings.embed_model, "prompt": text},
            )
            resp.raise_for_status()
            results.append(resp.json()["embedding"])
    return results
