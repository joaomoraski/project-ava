"""Chat history search tool — placeholder for B5 integration."""
from __future__ import annotations

import logging

from langchain_core.tools import tool

logger = logging.getLogger("tools.chat_search")


@tool
def search_chat_history(query: str, workspace: str = "personal") -> str:
    """Search past conversations for relevant information.

    Args:
        query: what to search for
        workspace: which workspace to search (default: personal)
    """
    # Full implementation in B5 when ChatManager is available
    try:
        from core.memory.chat_history import ChatManager
        manager = ChatManager(workspace)
        results = manager.search(query)
        if not results:
            return f"No past conversations found matching: {query}"
        return "\n\n".join(
            f"[{r['timestamp']}] {r['role']}: {r['content'][:200]}"
            for r in results[:5]
        )
    except ImportError:
        return "Chat history not yet available (implemented in B5)."
    except Exception as e:
        logger.error(f"chat_search failed: {e}")
        return f"Search failed: {e}"
