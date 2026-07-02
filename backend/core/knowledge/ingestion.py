"""Document ingestion pipeline — PDF, DOCX, TXT, URL, Markdown.

Extracts text from various sources and chunks it for vector indexing.
Supports: PDF (pymupdf), DOCX (python-docx), TXT/MD (plain text), URL (trafilatura).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterator

logger = logging.getLogger("core.knowledge.ingestion")

CHUNK_SIZE = 1000    # characters per chunk
CHUNK_OVERLAP = 200  # overlap between consecutive chunks


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks."""
    if not text.strip():
        return []

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - overlap
        if start >= len(text):
            break

    return chunks


def extract_pdf(path: str) -> str:
    """Extract text from a PDF file using pymupdf."""
    try:
        import fitz  # pymupdf
        doc = fitz.open(path)
        pages = [page.get_text() for page in doc]
        return "\n\n".join(pages)
    except ImportError:
        logger.error("pymupdf not installed. Run: pip install pymupdf")
        return ""
    except Exception as e:
        logger.error(f"PDF extraction failed for {path}: {e}")
        return ""


def extract_docx(path: str) -> str:
    """Extract text from a DOCX file."""
    try:
        from docx import Document
        doc = Document(path)
        return "\n\n".join(para.text for para in doc.paragraphs if para.text.strip())
    except ImportError:
        logger.error("python-docx not installed. Run: pip install python-docx")
        return ""
    except Exception as e:
        logger.error(f"DOCX extraction failed for {path}: {e}")
        return ""


def extract_text_file(path: str) -> str:
    """Extract plain text from a TXT or Markdown file."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception as e:
        logger.error(f"Text file extraction failed for {path}: {e}")
        return ""


def extract_url(url: str) -> str:
    """Extract main text content from a URL using trafilatura."""
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(downloaded, include_comments=False, include_tables=True)
            return text or ""
        return ""
    except ImportError:
        logger.error("trafilatura not installed. Run: pip install trafilatura")
        return ""
    except Exception as e:
        logger.error(f"URL extraction failed for {url}: {e}")
        return ""


def extract_file(path: str) -> str:
    """Auto-detect file type and extract text."""
    ext = Path(path).suffix.lower()
    extractors = {
        ".pdf": extract_pdf,
        ".docx": extract_docx,
        ".txt": extract_text_file,
        ".md": extract_text_file,
        ".markdown": extract_text_file,
        ".rst": extract_text_file,
        ".csv": extract_text_file,
    }
    extractor = extractors.get(ext)
    if not extractor:
        logger.warning(f"Unsupported file type: {ext} ({path})")
        return ""
    return extractor(path)


def iter_directory_files(directory: str) -> Iterator[str]:
    """Recursively yield supported file paths from a directory."""
    supported = {".pdf", ".docx", ".txt", ".md", ".markdown", ".rst"}
    for root, _, files in os.walk(directory):
        for fname in files:
            if Path(fname).suffix.lower() in supported:
                yield os.path.join(root, fname)


def ingest_file(path: str) -> list[dict]:
    """Ingest a single file → returns list of chunks with metadata."""
    text = extract_file(path)
    if not text:
        return []

    chunks = chunk_text(text)
    return [
        {
            "content": chunk,
            "source": path,
            "source_type": "file",
            "file_name": Path(path).name,
        }
        for chunk in chunks
    ]


def ingest_url(url: str) -> list[dict]:
    """Ingest a URL → returns list of chunks with metadata."""
    text = extract_url(url)
    if not text:
        return []

    chunks = chunk_text(text)
    return [
        {
            "content": chunk,
            "source": url,
            "source_type": "url",
            "file_name": url,
        }
        for chunk in chunks
    ]


def ingest_directory(directory: str) -> list[dict]:
    """Ingest all supported files in a directory."""
    all_chunks = []
    for file_path in iter_directory_files(directory):
        chunks = ingest_file(file_path)
        all_chunks.extend(chunks)
        if chunks:
            logger.info(f"Ingested: {file_path} → {len(chunks)} chunks")
    return all_chunks


def chunk_meeting_document(
    markdown: str,
    max_chars: int = 900,
    overlap_pct: float = 0.15,
    source: str = "",
    title: str = "",
) -> list[dict]:
    """Section-aware chunking for meeting Markdown documents.

    Respects section boundaries:
    - Never splits within a section header's content unless forced.
    - Prefers breaking at paragraph boundaries.
    - Falls back to sentence-level splits for very long paragraphs.

    Returns list of dicts with 'content', 'source', 'source_type', 'file_name'.
    """
    if not markdown.strip():
        return []

    # Split into top-level and second-level sections, preserving header lines.
    import re
    # Split on lines that start with # or ##, keeping the delimiter.
    raw_sections: list[str] = re.split(r"(?m)(?=^#{1,2} )", markdown)
    raw_sections = [s.strip() for s in raw_sections if s.strip()]

    def _split_sentences(text: str) -> list[str]:
        """Naive sentence splitter on '. ', '! ', '? '."""
        parts = re.split(r"(?<=[.!?])\s+", text)
        return [p.strip() for p in parts if p.strip()]

    def _hard_split(text: str, limit: int) -> list[str]:
        """Last-resort split: chunk at word boundary when no sentence punctuation exists."""
        if len(text) <= limit:
            return [text]
        words = text.split(" ")
        out: list[str] = []
        buf = ""
        for w in words:
            if buf and len(buf) + 1 + len(w) > limit:
                out.append(buf)
                buf = w
            else:
                buf = (buf + " " + w).strip() if buf else w
        if buf:
            out.append(buf)
        return out

    def _build_chunks(text: str) -> list[str]:
        """Split a section into chunks ≤ max_chars, preferring paragraph boundaries."""
        if len(text) <= max_chars:
            return [text]

        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        result: list[str] = []
        current = ""

        for para in paragraphs:
            if len(para) > max_chars:
                # Para too big — split at sentence boundaries
                if current:
                    result.append(current)
                    current = ""
                sentences = _split_sentences(para)
                buf = ""
                for sent in sentences:
                    # If a single "sentence" exceeds max_chars (no punctuation),
                    # hard-split it at word boundaries.
                    if len(sent) > max_chars:
                        if buf:
                            result.append(buf)
                            buf = ""
                        result.extend(_hard_split(sent, max_chars))
                        continue
                    if buf and len(buf) + 1 + len(sent) > max_chars:
                        result.append(buf)
                        buf = sent
                    else:
                        buf = (buf + " " + sent).strip() if buf else sent
                if buf:
                    result.append(buf)
            elif current and len(current) + 2 + len(para) > max_chars:
                result.append(current)
                current = para
            else:
                current = (current + "\n\n" + para).strip() if current else para

        if current:
            result.append(current)

        return result if result else [text[:max_chars]]

    # Collect all raw text chunks from sections
    raw_chunks: list[str] = []
    for section in raw_sections:
        raw_chunks.extend(_build_chunks(section))

    if not raw_chunks:
        return []

    # Apply overlap: prepend tail of previous chunk
    overlap_chars = max(0, int(max_chars * overlap_pct))
    final_chunks: list[dict] = []
    for i, chunk in enumerate(raw_chunks):
        if i > 0 and overlap_chars > 0:
            prev = raw_chunks[i - 1]
            tail = prev[-overlap_chars:].strip()
            # Only prepend if it doesn't duplicate the start of current chunk
            if tail and not chunk.startswith(tail):
                chunk = tail + "\n\n" + chunk
        final_chunks.append({
            "content": chunk,
            "source": source,
            "source_type": "meeting",
            "file_name": title,
        })

    return final_chunks
