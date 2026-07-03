"""Smart Transcription Priority — topic-aware processing depth.

After STT transcribes a segment, this classifies the topic and decides
how deeply to process it based on workspace config.

Priority levels:
  high   → full transcription, save to notes, generate action items, index in KB
  medium → save transcription, no notes, no indexing
  low    → skip or one-line summary

The classification is done via a lightweight LLM pass.
Falls back to heuristic keyword matching if LLM is unavailable.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("core.stt.priority")


class TranscriptionPriority:
    """Classifies transcriptions by topic and decides processing depth.

    Instantiated per workspace with the workspace's transcription_priority config.
    """

    def __init__(
        self,
        workspace_config: dict[str, Any],
        llm=None,
    ) -> None:
        priority_cfg = workspace_config.get("transcription_priority", {})
        self._mode = priority_cfg.get("mode", "smart")
        self._high_topics: list[str] = priority_cfg.get("high_priority_topics", [])
        self._low_topics: list[str] = priority_cfg.get("low_priority_topics", [])
        self._behavior: str = priority_cfg.get("behavior", "")
        self._llm = llm

    def set_llm(self, llm) -> None:
        self._llm = llm

    async def classify_and_process(self, transcript: str) -> dict[str, Any]:
        """Classify a transcript and return processing instructions.

        Returns:
            dict with keys:
              - priority: 'high' | 'medium' | 'low'
              - action: 'full' | 'save' | 'skip'
              - save: bool — whether to save the transcript
              - notes: bool — whether to generate notes/action items
              - index: bool — whether to index in knowledge base
              - reason: str — explanation of the decision
        """
        if self._mode == "off":
            return self._make_decision("low", reason="priority mode is off")

        if self._mode == "always_on":
            return self._make_decision("high", reason="always_on mode")

        # smart mode: classify topic
        priority, reason = await self._classify(transcript)
        return self._make_decision(priority, reason=reason)

    async def _classify(self, transcript: str) -> tuple[str, str]:
        """Classify priority via LLM or heuristic fallback."""
        # Try LLM classification first
        if self._llm:
            try:
                priority, reason = await self._llm_classify(transcript)
                return priority, reason
            except Exception as e:
                logger.warning(f"LLM classification failed: {e} — using heuristic.")

        # Heuristic keyword matching
        return self._heuristic_classify(transcript)

    async def _llm_classify(self, transcript: str) -> tuple[str, str]:
        """Use LLM to classify the transcript topic."""
        from langchain_core.messages import HumanMessage

        high_str = ", ".join(self._high_topics) or "tasks, meetings, work, decisions, deadlines"
        low_str = ", ".join(self._low_topics) or "casual chat, off-topic, entertainment"

        prompt = f"""Classify this transcript as 'high', 'medium', or 'low' priority.

High priority topics: {high_str}
Low priority topics: {low_str}

Transcript: "{transcript}"

Reply with ONLY one of: high, medium, low
Then a brief reason on the same line, e.g.: "high: discusses deadline for project"
"""
        result = await self._llm.ainvoke([HumanMessage(content=prompt)])
        text = (result.content if hasattr(result, "content") else str(result)).strip().lower()

        # Parse response
        for level in ("high", "medium", "low"):
            if text.startswith(level):
                reason = text[len(level):].lstrip(":").strip() or f"classified as {level}"
                return level, reason

        return "medium", "could not parse LLM classification"

    def _heuristic_classify(self, transcript: str) -> tuple[str, str]:
        """Keyword-based classification fallback."""
        text_lower = transcript.lower()

        # Check high priority keywords
        for topic in self._high_topics:
            if topic.lower() in text_lower:
                return "high", f"matched high-priority topic: {topic}"

        # Default high-priority keywords
        HIGH_KEYWORDS = [
            "deadline", "meeting", "task", "action item", "decision", "bug",
            "deploy", "release", "urgent", "important", "schedule", "review",
        ]
        for kw in HIGH_KEYWORDS:
            if kw in text_lower:
                return "high", f"matched keyword: {kw}"

        # Check low priority keywords
        for topic in self._low_topics:
            if topic.lower() in text_lower:
                return "low", f"matched low-priority topic: {topic}"

        # Default low-priority patterns
        LOW_PATTERNS = [
            r"\b(haha|lol|hehe)\b",
            r"\b(movie|series|netflix|youtube|game|sport)\b",
        ]
        for pattern in LOW_PATTERNS:
            if re.search(pattern, text_lower):
                return "low", f"matched low-priority pattern"

        return "medium", "no specific topic match"

    def _make_decision(self, priority: str, reason: str = "") -> dict[str, Any]:
        """Convert priority level to processing instructions."""
        decisions = {
            "high": {
                "priority": "high",
                "action": "full",
                "save": True,
                "notes": True,
                "index": True,
                "reason": reason,
            },
            "medium": {
                "priority": "medium",
                "action": "save",
                "save": True,
                "notes": False,
                "index": False,
                "reason": reason,
            },
            "low": {
                "priority": "low",
                "action": "skip",
                "save": False,
                "notes": False,
                "index": False,
                "reason": reason,
            },
        }
        return decisions.get(priority, decisions["medium"])
