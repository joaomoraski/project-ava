"""Sentence buffer for streaming LLM → TTS pipeline.

Accumulates LLM tokens and flushes complete sentences to TTS,
enabling smooth streaming audio without waiting for the full response.

Flush triggers:
  1. Sentence-ending punctuation: . ! ? \n
  2. Soft delimiter (,;:) when buffer > SOFT_FLUSH_MIN_CHARS
  3. Hard flush at FORCE_FLUSH_CHARS (prevents very long sentences blocking TTS)
"""
from __future__ import annotations

import re
from typing import AsyncIterator, Iterator

SENTENCE_END = re.compile(r'[.!?\n]')
SOFT_DELIMITER = re.compile(r'[,;:]')
SOFT_FLUSH_MIN_CHARS = 60
FORCE_FLUSH_CHARS = 200


class SentenceBuffer:
    """Collects LLM token stream and yields complete sentences for TTS."""

    def __init__(
        self,
        soft_flush_min: int = SOFT_FLUSH_MIN_CHARS,
        force_flush_at: int = FORCE_FLUSH_CHARS,
    ) -> None:
        self._buffer: str = ""
        self._soft_flush_min = soft_flush_min
        self._force_flush_at = force_flush_at

    def feed(self, token: str) -> list[str]:
        """Feed a token and return any complete sentences ready for TTS."""
        self._buffer += token
        return self._flush_ready()

    def _flush_ready(self) -> list[str]:
        sentences: list[str] = []

        while True:
            # Check hard flush first
            if len(self._buffer) >= self._force_flush_at:
                chunk, self._buffer = self._buffer, ""
                sentences.append(chunk.strip())
                continue

            # Check sentence-ending punctuation
            match = SENTENCE_END.search(self._buffer)
            if match:
                end_pos = match.end()
                chunk = self._buffer[:end_pos].strip()
                self._buffer = self._buffer[end_pos:].lstrip()
                if chunk:
                    sentences.append(chunk)
                continue

            # Check soft delimiter
            if len(self._buffer) >= self._soft_flush_min:
                match = SOFT_DELIMITER.search(self._buffer)
                if match:
                    end_pos = match.end()
                    chunk = self._buffer[:end_pos].strip()
                    self._buffer = self._buffer[end_pos:].lstrip()
                    if chunk:
                        sentences.append(chunk)
                    continue

            break

        return sentences

    def flush_remaining(self) -> str | None:
        """Force-flush any remaining buffer content at end of response."""
        if self._buffer.strip():
            remaining = self._buffer.strip()
            self._buffer = ""
            return remaining
        self._buffer = ""
        return None

    def reset(self) -> None:
        self._buffer = ""

    @property
    def buffer(self) -> str:
        return self._buffer


async def stream_sentences(
    token_stream: AsyncIterator[str],
    soft_flush_min: int = SOFT_FLUSH_MIN_CHARS,
    force_flush_at: int = FORCE_FLUSH_CHARS,
) -> AsyncIterator[str]:
    """Async generator: converts token stream → sentence stream for TTS.

    Usage:
        async for sentence in stream_sentences(llm_stream):
            await tts.synthesize(sentence)
    """
    buf = SentenceBuffer(soft_flush_min, force_flush_at)
    async for token in token_stream:
        for sentence in buf.feed(token):
            yield sentence
    # Flush remaining
    remaining = buf.flush_remaining()
    if remaining:
        yield remaining
