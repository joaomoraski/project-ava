"""LangGraph agent with tool calling and streaming support.

Uses create_react_agent from LangGraph for proper tool execution loop.
Tools are injected per-workspace by the PluginRegistry.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, AsyncIterator

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_core.tools import BaseTool

from core.llm.provider import get_llm

if TYPE_CHECKING:
    from core.memory.summarizer import ConversationSummarizer

logger = logging.getLogger("core.llm.agent")

ANTI_HALLUCINATION_PROMPT = (
    "\n\nIf you are not certain about a fact the user asks about — especially names, "
    "dates, numbers, or specifics from prior meetings, notes, or todos — DO NOT guess. "
    "Call the appropriate search tool again (`search_meetings`, `knowledge_search`, "
    "`get_context`, `search_chat_history`) with fresh terms. Say \"checking...\" and "
    "re-search. Only answer with information you just retrieved or can clearly derive "
    "from context."
)


def _count_tokens(messages: list[BaseMessage], model_name: str = "") -> int:
    """Approximate token count for a list of messages."""
    try:
        import tiktoken
        try:
            enc = tiktoken.encoding_for_model(model_name)
        except Exception:
            enc = tiktoken.get_encoding("cl100k_base")
        total = 0
        for msg in messages:
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            total += len(enc.encode(content)) + 4  # ~4 tokens per message overhead
        return total
    except ImportError:
        # Fallback: approximate 1 token per 4 chars
        total = 0
        for msg in messages:
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            total += len(content) // 4
        return total


class AvaAgent:
    """LangGraph-backed conversational agent with tool support.

    Uses create_react_agent for reliable tool calling across all LLM providers
    (Ollama, OpenAI, Anthropic, Google). Falls back to direct LLM streaming
    when no tools are configured.
    """

    def __init__(
        self,
        tools: list[BaseTool] | None = None,
        system_prompt: str | None = None,
        summarizer: ConversationSummarizer | None = None,
    ) -> None:
        self._tools = tools or []
        self._system_prompt = system_prompt
        self._summarizer = summarizer
        self._graph = None
        self._llm = None
        self._build()

    def _build(self) -> None:
        """Build the agent graph or plain LLM."""
        self._llm = get_llm(streaming=True)

        if self._tools:
            try:
                from langgraph.prebuilt import create_react_agent
                from core.config import settings

                # Warn if model is too small for reliable tool calling
                model_name = (settings.llm_model or "").lower()
                small_models = ["llama3.2", "llama3.2:1b", "llama3.2:3b", "phi3:mini", "tinyllama"]
                if any(sm in model_name for sm in small_models):
                    logger.warning(
                        f"Model '{settings.llm_model}' is small (<=3B params) and may not reliably "
                        "call tools. Recommended: qwen2.5:7b, llama3.1:8b, mistral:7b, or a cloud model."
                    )

                # Bind tools to LLM first to verify compatibility
                try:
                    self._llm_with_tools = self._llm.bind_tools(self._tools)
                    logger.info(f"LLM tools bound successfully: {[t.name for t in self._tools]}")
                except Exception as e:
                    logger.warning(f"bind_tools failed (model may not support tool calling): {e}")

                # Append context tool instructions to system prompt
                full_prompt = self._system_prompt or ""
                context_hint = (
                    "\n\nWhen the user references a context, project, or topic by name "
                    "(e.g. 'the Tabby context', 'check the X project'), call "
                    "`get_context(name=..., workspace=current_workspace)`. Contexts bundle "
                    "meetings, notes, todos, and action items around a theme — use the "
                    "returned content to answer with specific facts rather than generalities."
                )
                if "get_context" not in full_prompt:
                    full_prompt = full_prompt + context_hint
                if "DO NOT guess" not in full_prompt:
                    full_prompt = full_prompt + ANTI_HALLUCINATION_PROMPT

                self._graph = create_react_agent(
                    self._llm,
                    tools=self._tools,
                    prompt=full_prompt or None,
                )
                logger.info(f"React agent created with {len(self._tools)} tools: {[t.name for t in self._tools]}")
            except Exception as e:
                logger.error(f"create_react_agent failed, using plain LLM: {e}", exc_info=True)
                self._graph = None
        else:
            logger.info("No tools provided — using plain LLM (no tool calling).")
            self._graph = None

    def update_tools(self, tools: list[BaseTool]) -> None:
        self._tools = tools
        self._build()

    def update_system_prompt(self, system_prompt: str) -> None:
        self._system_prompt = system_prompt

    async def _maybe_compact(self, messages: list[BaseMessage]) -> list[BaseMessage]:
        """Compact messages to fit within token budget using summarizer if available."""
        from core.config import settings

        model_name = settings.llm_model or ""
        token_count = _count_tokens(messages, model_name)

        if token_count <= settings.max_context_tokens:
            return messages

        if not self._summarizer:
            # No summarizer — just truncate to last 20 messages
            logger.warning(
                f"Context too large ({token_count} tokens) but no summarizer — truncating to last 20 messages."
            )
            return messages[-20:]

        keep_recent = messages[-20:]
        old = messages[:-20]

        # Convert BaseMessage list to dict format for summarizer
        old_dicts = []
        for m in old:
            if isinstance(m, HumanMessage):
                old_dicts.append({"role": "user", "content": m.content if isinstance(m.content, str) else str(m.content)})
            elif isinstance(m, AIMessage):
                old_dicts.append({"role": "assistant", "content": m.content if isinstance(m.content, str) else str(m.content)})
            elif isinstance(m, SystemMessage):
                old_dicts.append({"role": "system", "content": m.content if isinstance(m.content, str) else str(m.content)})

        try:
            raw_summary = await self._summarizer.summarize(old_dicts)
            entities, summary_text = self._summarizer._parse_summary(raw_summary)
            formatted = (
                f"Earlier conversation summary ({len(old_dicts)} older messages compacted).\n"
                f"Entities mentioned: {entities}.\n"
                f"Summary: {summary_text}"
            )
            logger.info(
                f"Compacted {len(old_dicts)} messages into summary ({token_count} -> ~{_count_tokens(keep_recent, model_name)} tokens)."
            )
            return [SystemMessage(content=formatted), *keep_recent]
        except Exception as e:
            logger.error(f"Compaction failed: {e} — using raw truncation.")
            return keep_recent

    async def astream_tokens(
        self,
        user_input: str,
        history: list[BaseMessage] | None = None,
    ) -> AsyncIterator[str]:
        """Stream response tokens. Handles tool calls internally via LangGraph."""
        messages: list[BaseMessage] = []

        if history:
            messages.extend(history)

        messages.append(HumanMessage(content=user_input))

        messages = await self._maybe_compact(messages)

        if self._graph and self._tools:
            async for token in self._stream_via_graph(messages):
                yield token
        else:
            async for token in self._stream_plain(messages):
                yield token

    async def _stream_via_graph(self, messages: list[BaseMessage]) -> AsyncIterator[str]:
        """Stream via LangGraph react agent (handles tool loop automatically)."""
        input_state = {"messages": messages}
        config = {}

        try:
            async for event in self._graph.astream_events(input_state, config=config, version="v2"):
                kind = event.get("event", "")

                if kind == "on_tool_start":
                    tool_name = event.get("name", "unknown")
                    logger.info(f"Agent calling tool: {tool_name}")

                elif kind == "on_tool_end":
                    tool_name = event.get("name", "unknown")
                    output = event.get("data", {}).get("output", "")
                    logger.info(f"Tool {tool_name} returned {len(str(output))} chars")

                elif kind == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk")
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        # Only yield text content, not tool call artifacts
                        if isinstance(chunk.content, str):
                            yield chunk.content
                        elif isinstance(chunk.content, list):
                            for part in chunk.content:
                                if isinstance(part, str):
                                    yield part
                                elif isinstance(part, dict) and part.get("type") == "text":
                                    yield part.get("text", "")

        except Exception as e:
            logger.error(f"Graph streaming error: {e}", exc_info=True)
            # Fallback to plain LLM
            async for token in self._stream_plain(messages):
                yield token

    async def _stream_plain(self, messages: list[BaseMessage]) -> AsyncIterator[str]:
        """Stream directly from LLM without tools."""
        all_messages: list[BaseMessage] = []

        if self._system_prompt:
            all_messages.append(SystemMessage(content=self._system_prompt))

        all_messages.extend(messages)

        async for chunk in self._llm.astream(all_messages):
            if hasattr(chunk, "content") and chunk.content:
                yield chunk.content

    async def invoke(
        self,
        user_input: str,
        history: list[BaseMessage] | None = None,
    ) -> str:
        """Non-streaming invoke — returns full response string."""
        parts = []
        async for token in self.astream_tokens(user_input, history):
            parts.append(token)
        return "".join(parts)
