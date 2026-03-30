"""RAG pipeline: ChromaDB vector store + nomic-embed-text + cross-encoder reranking.

Vector search returns top-K candidates.
Cross-encoder reranker reranks and returns top-5.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from core.config import settings

logger = logging.getLogger("core.knowledge.rag")

COLLECTION_PREFIX = "ava"
TOP_K_RETRIEVAL = 20   # initial vector search candidates
TOP_K_RERANK = 5       # final results after reranking
RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"


def _chroma_path(workspace: str) -> str:
    return f"workspaces/{workspace}/chroma_db"


def _global_chroma_path() -> str:
    return "knowledge/chroma_db"


class KnowledgeBase:
    """Per-workspace ChromaDB collection with nomic-embed-text embeddings.

    Provides:
    - add_chunks(): index document chunks
    - search(): semantic search with optional cross-encoder reranking
    - delete_source(): remove all chunks from a specific source
    """

    def __init__(self, workspace: str, collection_name: str = "documents") -> None:
        self._workspace = workspace
        self._collection_name = collection_name
        self._client = None
        self._collection = None
        self._reranker = None
        self._embedding_fn = None

    def _get_client(self):
        if self._client is None:
            try:
                import chromadb
                self._client = chromadb.PersistentClient(path=_chroma_path(self._workspace))
            except ImportError:
                raise ImportError("chromadb not installed. Run: pip install chromadb")
        return self._client

    def _get_embedding_fn(self):
        if self._embedding_fn is None:
            try:
                from chromadb.utils.embedding_functions import OllamaEmbeddingFunction
                self._embedding_fn = OllamaEmbeddingFunction(
                    url=f"{settings.ollama_base_url}/api/embeddings",
                    model_name=settings.embed_model,
                )
            except Exception as e:
                logger.warning(f"Ollama embedding function unavailable: {e}. Using default.")
                self._embedding_fn = None  # chroma will use default
        return self._embedding_fn

    def _get_collection(self):
        if self._collection is None:
            client = self._get_client()
            embed_fn = self._get_embedding_fn()
            kwargs: dict[str, Any] = {
                "name": f"{COLLECTION_PREFIX}_{self._collection_name}",
                "metadata": {"workspace": self._workspace},
            }
            if embed_fn:
                kwargs["embedding_function"] = embed_fn
            self._collection = client.get_or_create_collection(**kwargs)
        return self._collection

    def _get_reranker(self):
        if self._reranker is None:
            try:
                from sentence_transformers import CrossEncoder
                self._reranker = CrossEncoder(RERANKER_MODEL)
                logger.debug("Cross-encoder reranker loaded.")
            except ImportError:
                logger.warning("sentence-transformers not installed — reranking disabled.")
                self._reranker = None
            except Exception as e:
                logger.warning(f"Reranker load failed: {e} — reranking disabled.")
                self._reranker = None
        return self._reranker

    def add_chunks(self, chunks: list[dict]) -> int:
        """Index chunks into ChromaDB.

        Args:
            chunks: list of dicts with 'content', 'source', 'source_type', 'file_name'

        Returns:
            Number of chunks indexed.
        """
        if not chunks:
            return 0

        collection = self._get_collection()
        documents = [c["content"] for c in chunks]
        metadatas = [
            {
                "source": c.get("source", ""),
                "source_type": c.get("source_type", "file"),
                "file_name": c.get("file_name", ""),
                "workspace": self._workspace,
            }
            for c in chunks
        ]
        ids = [str(uuid.uuid4()) for _ in chunks]

        try:
            collection.add(documents=documents, metadatas=metadatas, ids=ids)
            logger.info(f"Indexed {len(chunks)} chunks into {self._workspace}/{self._collection_name}")
            return len(chunks)
        except Exception as e:
            logger.error(f"ChromaDB add failed: {e}")
            return 0

    def search(self, query: str, top_k: int = TOP_K_RERANK) -> list[dict]:
        """Semantic search with optional cross-encoder reranking.

        Args:
            query: search query
            top_k: final number of results to return

        Returns:
            List of result dicts with 'content', 'source', 'score', 'workspace'
        """
        collection = self._get_collection()

        try:
            n_results = min(TOP_K_RETRIEVAL, collection.count())
            if n_results == 0:
                return []

            results = collection.query(
                query_texts=[query],
                n_results=n_results,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            logger.error(f"ChromaDB query failed: {e}")
            return []

        docs = results["documents"][0]
        metas = results["metadatas"][0]
        distances = results["distances"][0]

        if not docs:
            return []

        # Rerank with cross-encoder if available
        reranker = self._get_reranker()
        if reranker and len(docs) > 1:
            try:
                pairs = [(query, doc) for doc in docs]
                scores = reranker.predict(pairs)
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

    def delete_source(self, source: str) -> int:
        """Remove all chunks from a specific source URL or file path."""
        collection = self._get_collection()
        try:
            results = collection.get(where={"source": source})
            if results["ids"]:
                collection.delete(ids=results["ids"])
                logger.info(f"Deleted {len(results['ids'])} chunks from source: {source}")
                return len(results["ids"])
            return 0
        except Exception as e:
            logger.error(f"delete_source failed for {source}: {e}")
            return 0

    def count(self) -> int:
        """Return total number of indexed chunks."""
        try:
            return self._get_collection().count()
        except Exception:
            return 0

    def list_sources(self) -> list[dict]:
        """Return deduplicated list of indexed sources with chunk counts."""
        try:
            collection = self._get_collection()
            results = collection.get(include=["metadatas"])
            source_counts: dict[str, dict] = {}
            for meta in results.get("metadatas", []):
                source = meta.get("source", "")
                if source not in source_counts:
                    source_counts[source] = {
                        "source": source,
                        "source_type": meta.get("source_type", ""),
                        "file_name": meta.get("file_name", ""),
                        "chunk_count": 0,
                    }
                source_counts[source]["chunk_count"] += 1
            return list(source_counts.values())
        except Exception as e:
            logger.error(f"list_sources failed: {e}")
            return []
