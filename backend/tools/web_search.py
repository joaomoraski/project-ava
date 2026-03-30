"""Web search tool — DuckDuckGo (no API key required)."""
from __future__ import annotations

import logging

from langchain_core.tools import tool

logger = logging.getLogger("tools.web_search")


@tool
def web_search(query: str) -> str:
    """Search the web using DuckDuckGo. Returns top results as text.

    Args:
        query: the search query
    """
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        return "web_search unavailable: install duckduckgo-search (pip install duckduckgo-search)"

    try:
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=5):
                title = r.get("title", "")
                body = r.get("body", "")
                href = r.get("href", "")
                results.append(f"**{title}**\n{body}\n{href}")

        if not results:
            return "No results found."

        return "\n\n---\n\n".join(results)
    except Exception as e:
        logger.error(f"web_search failed: {e}")
        return f"Search failed: {e}"
