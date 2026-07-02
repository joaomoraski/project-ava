"""Knowledge base search tool for LLM agent."""
from __future__ import annotations

import logging

from langchain_core.tools import tool

logger = logging.getLogger("tools.knowledge_search")


@tool
async def knowledge_search(query: str, workspace: str = "personal") -> str:
    """Search the knowledge base for relevant documents, notes, and meeting transcripts.
    Use this for questions about the user's own data, uploaded documents, meeting notes,
    or any personal information they've added to the system.

    Args:
        query: what to search for
        workspace: which workspace to search (default: personal)
    """
    try:
        from core.db.engine import async_session
        from core.knowledge.rag import KnowledgeBase

        async with async_session() as session:
            kb = KnowledgeBase(workspace)
            results = await kb.search(session, query, top_k=5)

        if not results:
            return "No relevant documents found in the knowledge base."

        lines = []
        for r in results:
            source = r.get("source", "unknown")
            content = r.get("content", "")[:300]
            score = r.get("score", 0)
            lines.append(f"**Source:** {source} (score: {score})\n{content}")
        return "\n---\n".join(lines)
    except Exception as e:
        logger.error(f"knowledge_search failed: {e}")
        return f"Knowledge search failed: {e}"
