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
from typing import AsyncIterator, Iterator, NamedTuple

SENTENCE_END = re.compile(r'[.!?\n]')


class SentenceChunk(NamedTuple):
    """A sentence ready for TTS synthesis, with its detected language."""
    text: str
    language: str | None
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
    language: str | None = None,
    soft_flush_min: int = SOFT_FLUSH_MIN_CHARS,
    force_flush_at: int = FORCE_FLUSH_CHARS,
) -> AsyncIterator[SentenceChunk]:
    """Async generator: converts token stream → SentenceChunk stream for TTS.

    Args:
        token_stream: Async generator of LLM tokens.
        language: Language code detected by STT (e.g. "pt", "en"). If None,
                  TTS will auto-detect per sentence.

    Usage:
        async for chunk in stream_sentences(llm_stream, language="pt"):
            await tts.asynthesize(chunk.text, language=chunk.language)
    """
    buf = SentenceBuffer(soft_flush_min, force_flush_at)
    async for token in token_stream:
        for sentence in buf.feed(token):
            yield SentenceChunk(text=sentence, language=language)
    # Flush remaining
    remaining = buf.flush_remaining()
    if remaining:
        yield SentenceChunk(text=remaining, language=language)
