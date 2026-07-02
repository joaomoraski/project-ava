"""RAG pipeline: pgvector + nomic-embed-text + cross-encoder reranking.

Vector search via PostgreSQL cosine distance operator (<=>).
Cross-encoder reranker reranks and returns top-5.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select, delete, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.db.models import KnowledgeChunk, Workspace
from core.db.embeddings import generate_embedding, generate_embeddings_batch

logger = logging.getLogger("core.knowledge.rag")

TOP_K_RETRIEVAL = 20
TOP_K_RERANK = 5
RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"


class KnowledgeBase:
    """Per-workspace knowledge base backed by pgvector."""

    def __init__(self, workspace: str, collection_name: str = "documents") -> None:
        self._workspace = workspace
        self._collection_name = collection_name
        self._reranker = None

    def _get_reranker(self):
        if self._reranker is None:
            try:
                from sentence_transformers import CrossEncoder
                self._reranker = CrossEncoder(RERANKER_MODEL)
                logger.debug("Cross-encoder reranker loaded.")
            except ImportError:
                logger.warning("sentence-transformers not installed — reranking disabled.")
            except Exception as e:
                logger.warning(f"Reranker load failed: {e} — reranking disabled.")
        return self._reranker

    async def _get_workspace_id(self, session: AsyncSession) -> uuid.UUID | None:
        result = await session.execute(
            select(Workspace.id).where(Workspace.name == self._workspace)
        )
        return result.scalar_one_or_none()

    async def add_chunks(self, session: AsyncSession, chunks: list[dict]) -> int:
        """Index chunks into pgvector.

        Args:
            chunks: list of dicts with 'content', 'source', 'source_type', 'file_name'

        Returns:
            Number of chunks indexed.
        """
        if not chunks:
            return 0

        workspace_id = await self._get_workspace_id(session)
        if workspace_id is None:
            logger.error(f"Workspace '{self._workspace}' not found.")
            return 0

        texts = [c["content"] for c in chunks]

        try:
            embeddings = await generate_embeddings_batch(texts)
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            return 0

        try:
            for chunk, embedding in zip(chunks, embeddings):
                row = KnowledgeChunk(
                    workspace_id=workspace_id,
                    collection=self._collection_name,
                    content=chunk["content"],
                    embedding=embedding,
                    source=chunk.get("source", ""),
                    source_type=chunk.get("source_type", "file"),
                    file_name=chunk.get("file_name", ""),
                )
                session.add(row)

            await session.commit()
            logger.info(f"Indexed {len(chunks)} chunks into {self._workspace}/{self._collection_name}")
            return len(chunks)
        except Exception as e:
            await session.rollback()
            logger.error(f"pgvector add failed: {e}")
            return 0

    async def search(self, session: AsyncSession, query: str, top_k: int = TOP_K_RERANK) -> list[dict]:
        """Semantic search with optional cross-encoder reranking."""
        workspace_id = await self._get_workspace_id(session)
        if workspace_id is None:
            return []

        try:
            query_embedding = await generate_embedding(query)
        except Exception as e:
            logger.error(f"Query embedding failed: {e}")
            return []

        try:
            # pgvector cosine distance: <=> operator
            distance = KnowledgeChunk.embedding.cosine_distance(query_embedding)
            result = await session.execute(
                select(
                    KnowledgeChunk.content,
                    KnowledgeChunk.source,
                    KnowledgeChunk.source_type,
                    KnowledgeChunk.file_name,
                    distance.label("distance"),
                )
                .where(
                    KnowledgeChunk.workspace_id == workspace_id,
                    KnowledgeChunk.embedding.isnot(None),
                )
                .order_by(distance)
                .limit(TOP_K_RETRIEVAL)
            )
            rows = result.all()
        except Exception as e:
            logger.error(f"pgvector query failed: {e}")
            return []

        if not rows:
            return []

        docs = [r.content for r in rows]
        metas = [{"source": r.source, "source_type": r.source_type} for r in rows]
        distances = [r.distance for r in rows]

        # Rerank with cross-encoder if available
        reranker = self._get_reranker()
        if reranker and len(docs) > 1:
            try:
                import asyncio
                loop = asyncio.get_event_loop()
                pairs = [(query, doc) for doc in docs]
                scores = await loop.run_in_executor(None, reranker.predict, pairs)
                ranked = sorted(zip(scores, docs, metas), key=lambda x: x[0], reverse=True)
                docs = [r[1] for r in ranked[:top_k]]
                metas = [r[2] for r in ranked[:top_k]]
                scores_final = [float(r[0]) for r in ranked[:top_k]]
            except Exception as e:
                logger.warning(f"Reranking failed: {e} — using vector scores.")
                docs = docs[:top_k]
                metas = metas[:top_k]
                scores_final = [1.0 - d for d in distances[:top_k]]
        else:
            docs = docs[:top_k]
            metas = metas[:top_k]
            scores_final = [1.0 - d for d in distances[:top_k]]

        return [
            {
                "content": doc,
                "source": meta.get("source", ""),
                "source_type": meta.get("source_type", ""),
                "workspace": self._workspace,
                "score": round(score, 4),
            }
            for doc, meta, score in zip(docs, metas, scores_final)
        ]

    async def delete_source(self, session: AsyncSession, source: str) -> int:
        workspace_id = await self._get_workspace_id(session)
        if workspace_id is None:
            return 0

        result = await session.execute(
            delete(KnowledgeChunk)
            .where(
                KnowledgeChunk.workspace_id == workspace_id,
                KnowledgeChunk.source == source,
            )
            .returning(KnowledgeChunk.id)
        )
        await session.commit()
        deleted = len(result.all())
        if deleted:
            logger.info(f"Deleted {deleted} chunks from source: {source}")
        return deleted

    async def count(self, session: AsyncSession) -> int:
        workspace_id = await self._get_workspace_id(session)
        if workspace_id is None:
            return 0
        result = await session.execute(
            select(func.count(KnowledgeChunk.id))
            .where(KnowledgeChunk.workspace_id == workspace_id)
        )
        return result.scalar_one()

    async def list_sources(self, session: AsyncSession) -> list[dict]:
        workspace_id = await self._get_workspace_id(session)
        if workspace_id is None:
            return []

        result = await session.execute(
            select(
                KnowledgeChunk.source,
                KnowledgeChunk.source_type,
                KnowledgeChunk.file_name,
                func.count(KnowledgeChunk.id).label("chunk_count"),
            )
            .where(KnowledgeChunk.workspace_id == workspace_id)
            .group_by(
                KnowledgeChunk.source,
                KnowledgeChunk.source_type,
                KnowledgeChunk.file_name,
            )
        )
        return [
            {
                "source": row.source,
                "source_type": row.source_type,
                "file_name": row.file_name,
                "chunk_count": row.chunk_count,
            }
            for row in result.all()
        ]
