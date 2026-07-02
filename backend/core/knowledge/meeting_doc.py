"""Meeting document auto-generation and knowledge-base embedding.

Generates a structured Markdown report from a meeting's transcript via LLM,
then chunks and embeds the result into the workspace's knowledge base.
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.models import Meeting, ActionItem, Workspace

logger = logging.getLogger("core.knowledge.meeting_doc")

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_REPORT_PROMPT = """\
Analyze this meeting transcript and generate a structured report.
Respond in the SAME LANGUAGE as the transcript.
Output ONLY the report — no preamble, no meta-commentary.

Transcript:
{transcript}

Output format (Markdown):
# Summary
[2-3 sentence summary in the same language as the transcript — THIS SECTION IS MANDATORY, always include it]

# Key Decisions
- [decision 1]
- [decision 2]

# Action Items
- [ ] [owner]: [description] (due: [date if mentioned])

# Topics Discussed
## [Topic 1]
[brief summary of discussion]

IMPORTANT: You MUST always include the "# Summary" section. Even if the transcript is short or unclear,
write at least one sentence describing what was discussed. Never omit this section.
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_section(markdown: str, heading: str) -> str:
    """Return the content under a top-level heading (everything until next #)."""
    pattern = rf"(?m)^# {re.escape(heading)}\s*\n(.*?)(?=\n# |\Z)"
    m = re.search(pattern, markdown, re.DOTALL)
    return m.group(1).strip() if m else ""


def _extract_bullet_lines(block: str) -> list[str]:
    """Return non-empty lines that start with - or *."""
    lines = []
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith(("- ", "* ")):
            lines.append(stripped[2:].strip())
    return lines


def _parse_action_item(raw: str) -> dict[str, str | None]:
    """Parse '[ ] owner: description (due: date)' into a dict."""
    # Strip checkbox if present
    raw = re.sub(r"^\[[ x]\]\s*", "", raw).strip()

    owner: str | None = None
    description = raw
    due_date: str | None = None

    # Extract due date
    due_match = re.search(r"\(due:\s*([^)]+)\)", raw)
    if due_match:
        due_date = due_match.group(1).strip()
        description = raw[: due_match.start()].strip()

    # Extract owner (pattern: "owner: rest")
    colon_idx = description.find(":")
    if colon_idx > 0:
        candidate = description[:colon_idx].strip()
        # Owner is a short name/word, not a sentence fragment
        if len(candidate.split()) <= 4:
            owner = candidate
            description = description[colon_idx + 1:].strip()

    return {"owner": owner, "description": description, "due_date": due_date}


# ---------------------------------------------------------------------------
# Task 1: generate meeting document
# ---------------------------------------------------------------------------

async def generate_meeting_document(meeting_id: str, session: AsyncSession) -> str:
    """Generate a structured Markdown report from a meeting's transcript.

    Called when a meeting is stopped.  Updates the Meeting row in-place and
    creates ActionItem rows.  Returns the Markdown string (or empty string on
    failure).
    """
    result = await session.execute(
        select(Meeting).where(Meeting.id == uuid.UUID(meeting_id))
    )
    meeting: Meeting | None = result.scalar_one_or_none()
    if meeting is None:
        logger.warning(f"generate_meeting_document: meeting {meeting_id} not found")
        return ""

    transcript = (meeting.transcript or "").strip()
    if not transcript:
        logger.info(f"Meeting {meeting_id} has no transcript — skipping document generation.")
        return ""

    # --- call LLM -----------------------------------------------------------
    markdown_output = ""
    try:
        from langchain_core.messages import HumanMessage
        from core.llm.provider import get_llm

        llm = get_llm(streaming=False)
        # Detect dominant language from transcript to instruct LLM
        pt_count = sum(1 for w in transcript.lower().split() if w in {"que", "não", "com", "para", "uma", "como", "isso", "está", "você", "eu"})
        lang_instruction = "Respond in Portuguese (pt-BR)." if pt_count > 5 else "Respond in the same language as the transcript."
        prompt = _REPORT_PROMPT.format(transcript=transcript[:4000]) + f"\n\nIMPORTANT: {lang_instruction}"
        llm_result = await llm.ainvoke([HumanMessage(content=prompt)])
        markdown_output = (
            llm_result.content if hasattr(llm_result, "content") else str(llm_result)
        ).strip()
    except Exception as exc:
        logger.warning(
            f"LLM call failed for meeting {meeting_id}: {exc} — saving raw transcript."
        )
        # Graceful degradation: store transcript as minimal document
        markdown_output = f"# Meeting Transcript\n\n{transcript}"

    # --- parse structured fields --------------------------------------------
    summary_block = _extract_section(markdown_output, "Summary")
    decisions_block = _extract_section(markdown_output, "Key Decisions")
    action_items_block = _extract_section(markdown_output, "Action Items")

    decision_lines = _extract_bullet_lines(decisions_block)
    action_item_lines = _extract_bullet_lines(action_items_block)

    # Filter out "None" / empty placeholders from LLM output
    _none_phrases = {"none", "none.", "n/a", "none specific", "none noted", "none mentioned",
                     "none specific mentioned in the transcript", "none explicit decisions were made",
                     "no decisions", "no action items", "nenhum", "nenhuma"}
    decision_lines = [d for d in decision_lines if d.strip().lower().rstrip(".") not in _none_phrases]
    action_item_lines = [a for a in action_item_lines if a.strip().lower().rstrip(".") not in _none_phrases]

    # --- summary fallback ---------------------------------------------------
    if not summary_block.strip():
        # Try first meaningful paragraph (skip heading lines)
        for line in markdown_output.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                summary_block = stripped[:200]
                break

    if not summary_block.strip():
        # Last resort: small LLM call
        logger.error("Summary fallback triggered for meeting %s", meeting_id)
        try:
            from langchain_core.messages import HumanMessage
            from core.llm.provider import get_llm

            fallback_llm = get_llm(streaming=False)
            fallback_prompt = (
                f"In one sentence, summarize this transcript: {transcript[:3000]}"
            )
            fb_result = await fallback_llm.ainvoke([HumanMessage(content=fallback_prompt)])
            fb_text = (
                fb_result.content if hasattr(fb_result, "content") else str(fb_result)
            ).strip()
            summary_block = fb_text[:200] if fb_text else "(summary unavailable)"
        except Exception as fb_exc:
            logger.error("Summary fallback LLM call failed for meeting %s: %s", meeting_id, fb_exc)
            summary_block = "(summary unavailable)"

    # --- update Meeting row -------------------------------------------------
    meeting.document_md = markdown_output
    meeting.summary = summary_block or "(summary unavailable)"
    meeting.decisions = [{"text": d} for d in decision_lines]

    # --- create ActionItem rows --------------------------------------------
    # Delete existing rows first so retries don't accumulate duplicates.
    from sqlalchemy import delete as _sa_delete
    await session.execute(
        _sa_delete(ActionItem).where(ActionItem.meeting_id == meeting.id)
    )

    ws_id = meeting.workspace_id
    for raw_ai in action_item_lines:
        parsed = _parse_action_item(raw_ai)
        if not parsed["description"] or parsed["description"].lower().strip(".") in _none_phrases:
            continue
        ai_row = ActionItem(
            meeting_id=meeting.id,
            workspace_id=ws_id,
            owner=parsed["owner"],
            description=parsed["description"],
            status="pending",
        )
        # Due date is text only — store as note in description if present
        if parsed["due_date"]:
            ai_row.description = f"{parsed['description']} (due: {parsed['due_date']})"
        session.add(ai_row)

    try:
        await session.commit()
        logger.info(
            f"Meeting {meeting_id}: document generated "
            f"({len(decision_lines)} decisions, {len(action_item_lines)} action items)"
        )
    except Exception as exc:
        await session.rollback()
        logger.error(f"DB commit failed for meeting document {meeting_id}: {exc}")

    return markdown_output


# ---------------------------------------------------------------------------
# Task 2: embed meeting document into knowledge base
# ---------------------------------------------------------------------------

async def embed_meeting_document(
    meeting_id: str,
    workspace: str,
    session: AsyncSession,
) -> None:
    """Chunk and embed the meeting document_md into the workspace knowledge base."""
    result = await session.execute(
        select(Meeting).where(Meeting.id == uuid.UUID(meeting_id))
    )
    meeting: Meeting | None = result.scalar_one_or_none()
    if meeting is None:
        logger.warning(f"embed_meeting_document: meeting {meeting_id} not found")
        return

    transcript = (meeting.transcript or "").strip()
    summary = (meeting.summary or "").strip()
    if not transcript:
        logger.info(f"Meeting {meeting_id} has no transcript — skipping embedding.")
        return

    _SUMMARY_PLACEHOLDER = "(summary unavailable)"
    if not summary or summary == _SUMMARY_PLACEHOLDER:
        logger.info(
            f"Meeting {meeting_id} has no real summary (placeholder or empty) — skipping embedding."
        )
        return

    source_key = f"meeting:{meeting_id}"
    title = meeting.title or f"Meeting {meeting_id}"

    # Only embed transcript + summary — NOT decisions/action items
    # (those are structured data for the UI, not useful for similarity search)
    embed_text = f"# {title}\n\n"
    if summary:
        embed_text += f"## Summary\n{summary}\n\n"
    embed_text += f"## Transcript\n{transcript}"

    try:
        from core.knowledge.ingestion import chunk_meeting_document
        from core.knowledge.rag import KnowledgeBase

        chunks = chunk_meeting_document(
            embed_text,
            source=source_key,
            title=title,
        )
        if not chunks:
            logger.info(f"Meeting {meeting_id}: no chunks produced — skipping.")
            return

        kb = KnowledgeBase(workspace, "meetings")
        # Remove stale/duplicate chunks before indexing so retries replace, not append.
        await kb.delete_source(session, source_key)
        indexed = await kb.add_chunks(session, chunks)
        logger.info(f"Meeting {meeting_id}: embedded {indexed} chunks into '{workspace}/meetings'.")
    except Exception as exc:
        logger.warning(f"Embedding failed for meeting {meeting_id}: {exc}")
