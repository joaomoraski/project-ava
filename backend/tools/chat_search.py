"""Chat history search tool for LLM agent."""
from __future__ import annotations

import logging

from langchain_core.tools import tool

logger = logging.getLogger("tools.chat_search")


@tool
async def search_chat_history(query: str, workspace: str = "personal") -> str:
    """Search past conversations for relevant information.

    Args:
        query: what to search for
        workspace: which workspace to search (default: personal)
    """
    try:
        from core.db.engine import async_session
        from core.memory.chat_history import ChatManager

        async with async_session() as session:
            manager = ChatManager(workspace)
            results = await manager.search(session, query)

        if not results:
            return f"No past conversations found matching: {query}"
        return "\n\n".join(
            f"[{r['timestamp']}] {r['role']}: {r['content'][:200]}"
            for r in results[:5]
        )
    except Exception as e:
        logger.error(f"chat_search failed: {e}")
        return f"Search failed: {e}"
