"""LangGraph agent with tool calling and streaming support.

Manages the conversation graph: user input → tool calls (if needed) → response.
Tools are injected per-workspace by the PluginRegistry.
"""
from __future__ import annotations

import logging
from typing import AsyncIterator

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from core.llm.provider import get_llm

logger = logging.getLogger("core.llm.agent")


class AvaAgent:
    """LangGraph-backed conversational agent with tool support.

    Supports:
    - Streaming token output via astream_tokens()
    - Tool calling (LangChain tools injected per workspace)
    - System prompt per workspace
    """

    def __init__(
        self,
        tools: list[BaseTool] | None = None,
        system_prompt: str | None = None,
    ) -> None:
        self._tools = tools or []
        self._system_prompt = system_prompt
        self._graph = None
        self._build_graph()

    def _build_graph(self) -> None:
        """Build the LangGraph agent graph."""
        try:
            from langgraph.prebuilt import create_react_agent
        except ImportError:
            raise ImportError("langgraph not installed. Run: pip install langgraph")

        llm = get_llm(streaming=True)

        if self._tools:
            llm = llm.bind_tools(self._tools)

        self._llm = llm

    def update_tools(self, tools: list[BaseTool]) -> None:
        """Hot-swap tools without rebuilding the full graph."""
        self._tools = tools
        self._build_graph()

    def update_system_prompt(self, system_prompt: str) -> None:
        self._system_prompt = system_prompt

    async def astream_tokens(
        self,
        user_input: str,
        history: list[BaseMessage] | None = None,
    ) -> AsyncIterator[str]:
        """Stream response tokens for a user message.

        Handles tool calls internally — only text tokens are yielded.

        Args:
            user_input: the user's message
            history: prior conversation messages

        Yields:
            Text tokens as they arrive.
        """
        messages: list[BaseMessage] = []

        if self._system_prompt:
            messages.append(SystemMessage(content=self._system_prompt))

        if history:
            messages.extend(history)

        messages.append(HumanMessage(content=user_input))

        # Stream with potential tool call loop
        async for token in self._stream_with_tools(messages):
            yield token

    async def _stream_with_tools(
        self,
        messages: list[BaseMessage],
    ) -> AsyncIterator[str]:
        """Internal: stream LLM response, executing tool calls mid-stream."""
        max_iterations = 5  # prevent infinite tool loops
        current_messages = list(messages)

        for iteration in range(max_iterations):
            accumulated_content = ""
            tool_calls = []
            current_ai_message = None

            # Stream the LLM response
            async for chunk in self._llm.astream(current_messages):
                # Accumulate tool calls
                if hasattr(chunk, "tool_call_chunks") and chunk.tool_call_chunks:
                    tool_calls.extend(chunk.tool_call_chunks)

                # Yield text tokens
                if hasattr(chunk, "content") and chunk.content:
                    accumulated_content += chunk.content
                    yield chunk.content

                current_ai_message = chunk

            # No tool calls → done
            if not tool_calls or not self._tools:
                break

            # Execute tool calls
            logger.debug(f"Executing {len(tool_calls)} tool call(s)")
            tool_results = await self._execute_tool_calls(tool_calls)

            # Add AI message + tool results to history for next iteration
            if current_ai_message:
                current_messages.append(current_ai_message)
            for result in tool_results:
                current_messages.append(result)

        else:
            logger.warning(f"Agent reached max tool iterations ({max_iterations})")

    async def _execute_tool_calls(self, tool_call_chunks: list) -> list[ToolMessage]:
        """Execute tool calls and return ToolMessage results."""
        results = []
        tool_map = {tool.name: tool for tool in self._tools}

        for call in tool_call_chunks:
            tool_name = call.get("name") if isinstance(call, dict) else getattr(call, "name", None)
            tool_id = call.get("id") if isinstance(call, dict) else getattr(call, "id", None)
            tool_args = call.get("args") if isinstance(call, dict) else getattr(call, "args", {})

            if not tool_name or tool_name not in tool_map:
                logger.warning(f"Tool not found: {tool_name}")
                continue

            try:
                tool = tool_map[tool_name]
                if isinstance(tool_args, str):
                    import json
                    tool_args = json.loads(tool_args) if tool_args else {}

                result = await tool.arun(tool_args) if hasattr(tool, "arun") else tool.run(tool_args)
                results.append(ToolMessage(content=str(result), tool_call_id=tool_id or ""))
                logger.debug(f"Tool {tool_name} returned: {str(result)[:100]}")
            except Exception as e:
                logger.error(f"Tool {tool_name} failed: {e}")
                results.append(ToolMessage(content=f"Error: {e}", tool_call_id=tool_id or ""))

        return results

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
