"""LLM abstraction. Messages use the Anthropic Messages API block format."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class Reply:
    """One assistant turn: a list of `text` / `tool_use` content blocks."""

    content: list[dict]

    @property
    def text(self) -> str:
        return "\n".join(b["text"] for b in self.content if b.get("type") == "text").strip()

    @property
    def tool_calls(self) -> list[ToolCall]:
        return [
            ToolCall(b["id"], b["name"], b.get("input") or {})
            for b in self.content
            if b.get("type") == "tool_use"
        ]


class LLM(Protocol):
    def complete(
        self, system: str, messages: list[dict], tools: list[dict]
    ) -> Reply:
        """Run one model turn and return the assistant reply."""
        ...


class ClaudeLLM:
    """Anthropic backend. Requires ANTHROPIC_API_KEY in the environment."""

    def __init__(self, model: str = "claude-sonnet-5-5", max_tokens: int = 4096) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self._client = None

    def _get_client(self):
        if self._client is None:
            from anthropic import Anthropic

            self._client = Anthropic()
        return self._client

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> Reply:
        kwargs = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
        resp = self._get_client().messages.create(**kwargs)
        blocks = [
            b.model_dump(exclude_none=True)
            for b in resp.content
            if b.type in ("text", "tool_use")
        ]
        return Reply(blocks)
