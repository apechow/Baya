"""
Anthropic-backed ChatModel implementation.
"""

import os
from typing import List, Sequence

from agent.common.types import ChatMessage, ChatModel


class AnthropicChatModel(ChatModel):
    """ChatModel implementation using Anthropic's Messages API."""

    def __init__(self, model: str | None = None, max_tokens: int = 2048) -> None:
        try:
            import anthropic  # type: ignore
        except ImportError as exc:  # pragma: no cover - import guard
            raise RuntimeError(
                "Anthropic client library not installed. Install `anthropic` to use this backend."
            ) from exc

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY environment variable must be set for Anthropic backend.")

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-3-haiku-20240307")
        self.max_tokens = max_tokens

    def complete(self, messages: Sequence[ChatMessage]) -> str:
        system: str | None = None
        conversation: List[dict] = []

        for message in messages:
            if message.role == "system" and system is None:
                system = message.content
            else:
                conversation.append({"role": message.role, "content": message.content})

        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=conversation,
        )

        return "".join(block.text for block in response.content if getattr(block, "text", None))
