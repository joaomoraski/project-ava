"""Conversation summarizer — ConversationSummaryBufferMemory pattern.

Summarizes older conversation history to keep LLM context manageable.
Last N messages stay complete; older messages become a compact summary.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("core.memory.summarizer")

SUMMARY_PROMPT = """Summarize the following conversation concisely.

CRITICAL: preserve every proper noun the user mentioned — names of people,
projects, files, meetings, todos, places, products. List them verbatim at
the top of the summary under "Entities:". Never paraphrase entity names.

Then in 3-5 sentences, summarize what was discussed.

Conversation:
{history}

Output format:
Entities: <comma-separated list>
Summary: <3-5 sentences>"""


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
            Summary string (Entities: ... / Summary: ... format), or fallback.
        """
        if not messages:
            return ""

        history_text = "\n".join(
            f"{msg['role'].capitalize()}: {msg['content']}"
            for msg in messages
        )

        if not self._llm:
            logger.debug("No LLM for summarization — using truncation fallback.")
            return f"Entities: \nSummary: {len(messages)} messages exchanged (no LLM available to summarize)."

        prompt = SUMMARY_PROMPT.format(history=history_text)

        try:
            from langchain_core.messages import HumanMessage
            result = await self._llm.ainvoke([HumanMessage(content=prompt)])
            summary = result.content if hasattr(result, "content") else str(result)
            return summary.strip()
        except Exception as e:
            logger.error(f"Summarization failed: {e}")
            return f"Entities: \nSummary: {len(messages)} messages exchanged (summarization failed)."

    @staticmethod
    def _parse_summary(raw: str) -> tuple[str, str]:
        """Parse 'Entities: ...\nSummary: ...' format. Returns (entities, summary_text)."""
        entities = ""
        summary_text = raw
        for line in raw.splitlines():
            if line.startswith("Entities:"):
                entities = line[len("Entities:"):].strip()
            elif line.startswith("Summary:"):
                summary_text = line[len("Summary:"):].strip()
        return entities, summary_text

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
            entities, summary_text = self._parse_summary(summary)
            content = (
                f"Earlier conversation summary ({older_count} older messages compacted).\n"
                f"Entities mentioned: {entities}.\n"
                f"Summary: {summary_text}"
            )
            context.append({"role": "system", "content": content})
        elif older_count > 0 and not summary:
            context.append({
                "role": "system",
                "content": f"[{older_count} earlier messages not shown — no summary available]",
            })

        context.extend(recent)
        return context
