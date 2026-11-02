"""
OpenAI-backed ChatModel implementation.
"""

import os
from typing import Sequence

from agent.common.types import ChatMessage, ChatModel


class OpenAIChatModel(ChatModel):
    """ChatModel implementation using OpenAI's chat completions API."""

    def __init__(self, model: str | None = None, max_tokens: int = 2048) -> None:
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as exc:  # pragma: no cover - import guard
            raise RuntimeError(
                "OpenAI client library not installed. Install `openai` to use this backend."
            ) from exc

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY environment variable must be set for OpenAI backend.")

        self.client = OpenAI(api_key=api_key)
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.max_tokens = max_tokens

    def complete(self, messages: Sequence[ChatMessage]) -> str:
        payload = [{"role": message.role, "content": message.content} for message in messages]
        response = self.client.chat.completions.create(
            model=self.model,
            messages=payload,
            max_tokens=self.max_tokens,
        )
        choice = response.choices[0]
        return choice.message.content or ""
