"""Conversation summarizer — ConversationSummaryBufferMemory pattern.

Summarizes older conversation history to keep LLM context manageable.
Last N messages stay complete; older messages become a compact summary.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("core.memory.summarizer")

SUMMARY_PROMPT = """Summarize the following conversation history concisely.
Focus on: key decisions made, important information shared, topics discussed.
Be brief — this summary replaces older messages to save context space.

Conversation:
{history}

Summary:"""


class ConversationSummarizer:
    """Summarizes old conversation history via LLM."""

    def __init__(self, llm=None) -> None:
        self._llm = llm

    def set_llm(self, llm) -> None:
        self._llm = llm

    async def summarize(self, messages: list[dict[str, Any]]) -> str:
        """Summarize a list of messages into a compact string.

        Args:
            messages: list of {'role': str, 'content': str} dicts

        Returns:
            Summary string, or a simple concatenation if LLM unavailable.
        """
        if not messages:
            return ""

        history_text = "\n".join(
            f"{msg['role'].capitalize()}: {msg['content']}"
            for msg in messages
        )

        if not self._llm:
            # Fallback: simple truncation without LLM
            logger.debug("No LLM for summarization — using truncation fallback.")
            return f"[Previous conversation — {len(messages)} messages exchanged]"

        prompt = SUMMARY_PROMPT.format(history=history_text)

        try:
            from langchain_core.messages import HumanMessage
            result = await self._llm.ainvoke([HumanMessage(content=prompt)])
            summary = result.content if hasattr(result, "content") else str(result)
            return summary.strip()
        except Exception as e:
            logger.error(f"Summarization failed: {e}")
            return f"[Previous conversation — {len(messages)} messages exchanged]"

    def build_context(
        self,
        messages: list[dict[str, Any]],
        summary: str | None,
        max_recent: int = 20,
    ) -> list[dict[str, Any]]:
        """Build the context list for LLM: summary + last N messages.

        Args:
            messages: full message list
            summary: existing summary of older messages (or None)
            max_recent: number of recent messages to keep verbatim

        Returns:
            Context messages ready to pass to the LLM.
        """
        recent = messages[-max_recent:]
        older_count = len(messages) - len(recent)

        context: list[dict[str, Any]] = []

        if summary and older_count > 0:
            context.append({
                "role": "system",
                "content": f"[Conversation summary — {older_count} earlier messages]\n{summary}",
            })
        elif older_count > 0 and not summary:
            context.append({
                "role": "system",
                "content": f"[{older_count} earlier messages not shown — no summary available]",
            })

        context.extend(recent)
        return context
