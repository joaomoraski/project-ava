"""Tests for SentenceBuffer and stream_sentences."""
import pytest

from core.pipeline.sentence_buffer import SentenceBuffer, SentenceChunk, stream_sentences


class TestSentenceBufferFeed:
    def test_sentence_ending_period(self):
        buf = SentenceBuffer()
        sentences = buf.feed("Hello world.")
        assert sentences == ["Hello world."]
        assert buf.buffer == ""

    def test_sentence_ending_exclamation(self):
        buf = SentenceBuffer()
        sentences = buf.feed("Wow!")
        assert sentences == ["Wow!"]

    def test_sentence_ending_question(self):
        buf = SentenceBuffer()
        sentences = buf.feed("Is this working?")
        assert sentences == ["Is this working?"]

    def test_sentence_ending_newline(self):
        buf = SentenceBuffer()
        sentences = buf.feed("Line one\n")
        assert sentences == ["Line one"]

    def test_multiple_sentences_in_one_feed(self):
        buf = SentenceBuffer()
        sentences = buf.feed("First. Second. Third.")
        assert len(sentences) == 3
        assert sentences[0] == "First."
        assert sentences[1] == "Second."
        assert sentences[2] == "Third."

    def test_partial_token_no_flush(self):
        buf = SentenceBuffer()
        sentences = buf.feed("Hello ")
        assert sentences == []
        assert buf.buffer == "Hello "

    def test_accumulates_tokens(self):
        buf = SentenceBuffer()
        buf.feed("Hello ")
        buf.feed("world")
        sentences = buf.feed(".")
        assert sentences == ["Hello world."]

    def test_soft_flush_on_comma(self):
        buf = SentenceBuffer(soft_flush_min=10)
        # Feed enough to exceed soft_flush_min, then hit comma
        buf.feed("This is a long sentence,")
        sentences = buf.feed(" and more")
        # After "This is a long sentence," the buffer had 24 chars which exceeds soft_flush_min=10
        # The comma triggers soft flush
        # Actually soft flush happens when we call _flush_ready, which is called from feed
        # Let's just test the buffer contains remaining content
        buf2 = SentenceBuffer(soft_flush_min=10)
        result = buf2.feed("This is a long sentence,")
        assert len(result) == 1
        assert result[0] == "This is a long sentence,"

    def test_force_flush_at_limit(self):
        buf = SentenceBuffer(force_flush_at=20)
        long_text = "A" * 20
        sentences = buf.feed(long_text)
        assert len(sentences) == 1
        assert sentences[0] == long_text

    def test_strip_whitespace(self):
        buf = SentenceBuffer()
        sentences = buf.feed("  Hello.  ")
        assert sentences == ["Hello."]

    def test_lone_punctuation_yielded(self):
        # A lone period is non-empty after strip, so it is yielded
        buf = SentenceBuffer()
        sentences = buf.feed(".")
        assert sentences == ["."]

    def test_reset_clears_buffer(self):
        buf = SentenceBuffer()
        buf.feed("Partial text")
        buf.reset()
        assert buf.buffer == ""

    def test_flush_remaining_returns_content(self):
        buf = SentenceBuffer()
        buf.feed("No punctuation here")
        remaining = buf.flush_remaining()
        assert remaining == "No punctuation here"
        assert buf.buffer == ""

    def test_flush_remaining_empty_buffer(self):
        buf = SentenceBuffer()
        remaining = buf.flush_remaining()
        assert remaining is None

    def test_flush_remaining_whitespace_only(self):
        buf = SentenceBuffer()
        buf._buffer = "   "
        remaining = buf.flush_remaining()
        assert remaining is None


class TestStreamSentences:
    @pytest.mark.asyncio
    async def test_simple_sentence_stream(self):
        async def tokens():
            for t in ["Hello", " world", "."]:
                yield t

        result = []
        async for chunk in stream_sentences(tokens()):
            result.append(chunk)

        assert len(result) == 1
        assert result[0].text == "Hello world."
        assert result[0].language is None

    @pytest.mark.asyncio
    async def test_language_propagated(self):
        async def tokens():
            for t in ["Olá.", " Tudo bem?"]:
                yield t

        result = []
        async for chunk in stream_sentences(tokens(), language="pt"):
            result.append(chunk)

        assert len(result) == 2
        assert all(c.language == "pt" for c in result)

    @pytest.mark.asyncio
    async def test_multiple_sentences(self):
        async def tokens():
            for t in ["First.", " Second.", " Third."]:
                yield t

        result = []
        async for chunk in stream_sentences(tokens()):
            result.append(chunk)

        assert len(result) == 3
        assert [c.text for c in result] == ["First.", "Second.", "Third."]

    @pytest.mark.asyncio
    async def test_remaining_flushed_at_end(self):
        async def tokens():
            for t in ["No punctuation"]:
                yield t

        result = []
        async for chunk in stream_sentences(tokens()):
            result.append(chunk)

        assert len(result) == 1
        assert result[0].text == "No punctuation"

    @pytest.mark.asyncio
    async def test_empty_stream(self):
        async def tokens():
            return
            yield  # make it a generator

        result = []
        async for chunk in stream_sentences(tokens()):
            result.append(chunk)

        assert result == []

    @pytest.mark.asyncio
    async def test_custom_flush_parameters(self):
        async def tokens():
            yield "A" * 25  # > force_flush_at=20

        result = []
        async for chunk in stream_sentences(tokens(), force_flush_at=20):
            result.append(chunk)

        assert len(result) == 1
        assert len(result[0].text) == 25
