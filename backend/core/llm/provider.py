"""LLM provider factory — Ollama / OpenAI / Anthropic.

Selects the correct LangChain chat model based on LLM_PROVIDER in .env.
All providers return a LangChain BaseChatModel with streaming support.
"""
from __future__ import annotations

import logging
from typing import AsyncIterator

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage

from core.config import settings

logger = logging.getLogger("core.llm.provider")


def get_llm(streaming: bool = True):
    """Return the configured LangChain chat model.

    Args:
        streaming: enable token streaming (required for SentenceBuffer pipeline)

    Returns:
        A LangChain BaseChatModel instance.
    """
    provider = settings.llm_provider

    if provider == "ollama":
        try:
            from langchain_ollama import ChatOllama
        except ImportError:
            raise ImportError("langchain-ollama not installed. Run: pip install langchain-ollama")

        logger.debug(f"Using Ollama: {settings.llm_model} @ {settings.ollama_base_url}")
        return ChatOllama(
            model=settings.llm_model,
            base_url=settings.ollama_base_url,
            streaming=streaming,
        )

    if provider == "openai":
        if not settings.openai_api_key:
            raise ValueError("LLM_PROVIDER=openai but OPENAI_API_KEY is not set.")
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            raise ImportError("langchain-openai not installed. Run: pip install langchain-openai")

        logger.debug(f"Using OpenAI: {settings.openai_model}")
        return ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            streaming=streaming,
        )

    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ValueError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set.")
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError:
            raise ImportError("langchain-anthropic not installed. Run: pip install langchain-anthropic")

        logger.debug(f"Using Anthropic: {settings.anthropic_model}")
        return ChatAnthropic(
            model=settings.anthropic_model,
            api_key=settings.anthropic_api_key,
            streaming=streaming,
        )

    if provider == "google":
        if not settings.google_api_key:
            raise ValueError("LLM_PROVIDER=google but GOOGLE_API_KEY is not set.")
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except ImportError:
            raise ImportError("langchain-google-genai not installed. Run: pip install langchain-google-genai")

        logger.debug(f"Using Google: {settings.google_model}")
        return ChatGoogleGenerativeAI(
            model=settings.google_model,
            google_api_key=settings.google_api_key,
            streaming=streaming,
        )

    raise ValueError(f"Unknown LLM_PROVIDER: {provider}. Valid: ollama, openai, anthropic, google")


async def stream_tokens(
    messages: list[BaseMessage],
    system_prompt: str | None = None,
) -> AsyncIterator[str]:
    """Stream tokens from the LLM as an async generator.

    Args:
        messages: conversation messages (HumanMessage, AIMessage, etc.)
        system_prompt: optional system prompt prepended to messages

    Yields:
        Individual text tokens as they arrive from the model.
    """
    llm = get_llm(streaming=True)

    all_messages: list[BaseMessage] = []
    if system_prompt:
        all_messages.append(SystemMessage(content=system_prompt))
    all_messages.extend(messages)

    async for chunk in llm.astream(all_messages):
        if hasattr(chunk, "content") and chunk.content:
            yield chunk.content
