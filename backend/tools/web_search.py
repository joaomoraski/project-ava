"""Web search tool — Tavily (primary) + DuckDuckGo (fallback).

Tavily requires TAVILY_API_KEY in .env. When unavailable or rate-limited,
falls back to DuckDuckGo which needs no API key.
"""
from __future__ import annotations

import logging

from langchain_core.tools import tool

from core.config import settings

logger = logging.getLogger("tools.web_search")

_tavily_available: bool | None = None


def _check_tavily() -> bool:
    """Lazy check if Tavily is available."""
    global _tavily_available
    if _tavily_available is not None:
        return _tavily_available
    try:
        if not getattr(settings, "tavily_api_key", ""):
            _tavily_available = False
            return False
        from tavily import TavilyClient  # noqa: F401
        _tavily_available = True
    except ImportError:
        _tavily_available = False
    return _tavily_available


def _search_tavily(query: str, max_results: int = 5) -> str:
    """Search using Tavily API."""
    from tavily import TavilyClient
    client = TavilyClient(api_key=settings.tavily_api_key)
    response = client.search(query, max_results=max_results, search_depth="basic")

    results = []
    for r in response.get("results", []):
        title = r.get("title", "")
        content = r.get("content", "")
        url = r.get("url", "")
        results.append(f"**{title}**\n{content}\n{url}")

    if not results:
        return "No results found."
    return "\n\n---\n\n".join(results)


def _search_duckduckgo(query: str, max_results: int = 5) -> str:
    """Search using DuckDuckGo (no API key needed)."""
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        return "web_search unavailable: install duckduckgo-search"

    # Sanitize query — remove non-ASCII chars that break latin-1 encoding
    safe_query = query.encode("ascii", errors="ignore").decode("ascii").strip()
    if not safe_query:
        safe_query = query  # fallback to original if all chars are non-ASCII

    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(safe_query, max_results=max_results):
            title = r.get("title", "")
            body = r.get("body", "")
            href = r.get("href", "")
            results.append(f"**{title}**\n{body}\n{href}")

    if not results:
        return "No results found."
    return "\n\n---\n\n".join(results)


@tool
def web_search(query: str) -> str:
    """Search the web for current information. Use for questions about today's date, news, prices, events, or anything not in your training data.

    Args:
        query: the search query string
    """
    # Try Tavily first (better quality, structured results)
    if _check_tavily():
        try:
            result = _search_tavily(query)
            logger.debug(f"Tavily search: '{query}' → {len(result)} chars")
            return result
        except Exception as e:
            logger.warning(f"Tavily failed, falling back to DuckDuckGo: {e}")

    # Fallback to DuckDuckGo
    try:
        result = _search_duckduckgo(query)
        logger.debug(f"DuckDuckGo search: '{query}' → {len(result)} chars")
        return result
    except Exception as e:
        logger.error(f"web_search failed: {e}")
        return f"Search failed: {e}"
