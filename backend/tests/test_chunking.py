"""Tests for chunk_meeting_document() in core/knowledge/ingestion.py.

All tests are pure — no DB, no mocking, no network required.
"""
from __future__ import annotations

import pytest

from core.knowledge.ingestion import chunk_meeting_document

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

SAMPLE_MEETING_DOC = """\
# Summary
This was a sprint planning meeting. We discussed the Q2 roadmap and assigned tasks.

# Key Decisions
- Moving to weekly releases starting April
- Budget approved for cloud infrastructure upgrade
- Hiring two more backend engineers

# Action Items
- [ ] Alice: Update API documentation by Friday
- [ ] Bob: Fix authentication bug (#1234) by Wednesday
- [ ] Carol: Set up CI/CD pipeline for staging

# Topics Discussed
## Release Cadence
The team agreed that bi-weekly releases are too slow. Moving to weekly releases
with automated testing gates. Bob will set up the release automation.

## Q2 Roadmap
Three main priorities: performance optimization, mobile app launch, and
enterprise features. Each team lead will break down their area by next week.

## Infrastructure
Current costs are $4k/month. Expected to grow to $8k with the new features.
Carol will evaluate reserved instances vs spot instances.
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_chunk_simple_document():
    """Small document (well under max_chars) → 1 to a small number of chunks."""
    doc = "# Summary\nShort meeting. One item discussed.\n\n# Action Items\n- Do the thing."
    chunks = chunk_meeting_document(doc, max_chars=900)
    assert 1 <= len(chunks) <= 3
    # Combined content should contain the original text
    combined = " ".join(c["content"] for c in chunks)
    assert "Short meeting" in combined
    assert "Do the thing" in combined


def test_chunk_respects_sections():
    """Each # / ## section header should appear within at least one chunk."""
    chunks = chunk_meeting_document(SAMPLE_MEETING_DOC, max_chars=900)
    combined = "\n".join(c["content"] for c in chunks)
    for section in ("# Summary", "# Key Decisions", "# Action Items", "# Topics Discussed"):
        assert section in combined, f"Section header {section!r} missing from chunks"


def test_chunk_long_section():
    """A single section longer than max_chars must produce multiple chunks."""
    long_para = "This is a sentence that fills space. " * 200  # ~7 400 chars
    doc = f"# Long Section\n{long_para}"
    chunks = chunk_meeting_document(doc, max_chars=900)
    assert len(chunks) > 1, "Expected multiple chunks for a very long section"
    for chunk in chunks:
        # Each chunk content should not wildly exceed max_chars (allow for overlap prefix)
        assert len(chunk["content"]) < 900 * 3, "Chunk is unexpectedly large"


def test_chunk_empty_document():
    """Empty string → empty list (no crash)."""
    assert chunk_meeting_document("") == []
    assert chunk_meeting_document("   \n\n  ") == []


def test_chunk_overlap():
    """Consecutive chunks should share overlapping text at boundaries."""
    long_para = "Word " * 500  # 2 500 chars — forces multiple chunks
    doc = f"# Section\n{long_para}"
    chunks = chunk_meeting_document(doc, max_chars=900, overlap_pct=0.15)
    if len(chunks) < 2:
        pytest.skip("Not enough chunks produced to verify overlap")

    # Check that chunk[i] contains some text from the tail of chunk[i-1]
    found_overlap = False
    for i in range(1, len(chunks)):
        prev_tail = chunks[i - 1]["content"][-50:].strip()
        if prev_tail and prev_tail in chunks[i]["content"]:
            found_overlap = True
            break
    assert found_overlap, "No overlapping content detected between consecutive chunks"


def test_chunk_overlap_zero():
    """With overlap_pct=0, consecutive chunks should NOT share a prefix tail."""
    long_para = "Sentence number one. Sentence number two. Sentence number three. " * 50
    doc = f"# Section\n{long_para}"
    chunks = chunk_meeting_document(doc, max_chars=500, overlap_pct=0.0)
    if len(chunks) < 2:
        pytest.skip("Not enough chunks to check zero-overlap behaviour")
    # With zero overlap the tail of chunk[0] should NOT appear at the start of chunk[1]
    prev_tail = chunks[0]["content"][-80:].strip()
    # It's acceptable if the tail just isn't a prefix of the next chunk
    assert not chunks[1]["content"].startswith(prev_tail), (
        "Expected no overlap prefix with overlap_pct=0"
    )


def test_chunk_preserves_metadata():
    """source, source_type, and file_name metadata should be set on every chunk."""
    chunks = chunk_meeting_document(
        SAMPLE_MEETING_DOC,
        max_chars=900,
        source="/meetings/sprint.md",
        title="Sprint Planning",
    )
    assert chunks, "Expected at least one chunk"
    for i, chunk in enumerate(chunks):
        assert chunk["source"] == "/meetings/sprint.md", f"chunk {i}: wrong source"
        assert chunk["source_type"] == "meeting", f"chunk {i}: wrong source_type"
        assert chunk["file_name"] == "Sprint Planning", f"chunk {i}: wrong file_name"


def test_chunk_metadata_defaults():
    """source and title default to empty string when not provided."""
    chunks = chunk_meeting_document("# Section\nSome content here.")
    assert chunks
    assert chunks[0]["source"] == ""
    assert chunks[0]["file_name"] == ""
    assert chunks[0]["source_type"] == "meeting"


def test_chunk_real_meeting_document():
    """Full realistic meeting doc should produce a reasonable number of chunks."""
    chunks = chunk_meeting_document(SAMPLE_MEETING_DOC, max_chars=900)
    # The doc is ~700 chars; with section splitting we expect at least 2 and no more than 15
    assert 2 <= len(chunks) <= 15, f"Unexpected chunk count: {len(chunks)}"

    # All chunks must have non-empty content
    for i, chunk in enumerate(chunks):
        assert chunk["content"].strip(), f"chunk {i} is empty"

    # Key content should survive across chunks
    combined = "\n".join(c["content"] for c in chunks)
    assert "sprint planning" in combined.lower()
    assert "Alice" in combined
    assert "Bob" in combined
    assert "Carol" in combined


def test_chunk_single_section_fits():
    """When a section fits exactly in max_chars, it should be exactly one chunk."""
    doc = "# Notes\nShort content."
    chunks = chunk_meeting_document(doc, max_chars=900)
    assert len(chunks) == 1
    assert "Short content." in chunks[0]["content"]


def test_chunk_content_covers_full_document():
    """All significant words from the original doc appear somewhere in the chunks."""
    doc = "# A\nAlpha beta gamma.\n\n# B\nDelta epsilon zeta."
    chunks = chunk_meeting_document(doc, max_chars=900)
    combined = " ".join(c["content"] for c in chunks)
    for word in ["Alpha", "beta", "gamma", "Delta", "epsilon", "zeta"]:
        assert word in combined, f"Word {word!r} not found in any chunk"
