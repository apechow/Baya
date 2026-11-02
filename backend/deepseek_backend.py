"""
DeepSeek-backed ChatModel implementation.

DeepSeek exposes an OpenAI-compatible API, so this reuses the OpenAI client
pointed at DeepSeek's base URL.
"""

import os
from typing import Sequence

from agent.common.types import ChatMessage, ChatModel


class DeepSeekChatModel(ChatModel):
    """ChatModel implementation using DeepSeek's OpenAI-compatible chat API."""

    def __init__(self, model: str | None = None, max_tokens: int = 2048) -> None:
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as exc:  # pragma: no cover - import guard
            raise RuntimeError(
                "OpenAI client library not installed. Install `openai` to use the DeepSeek backend."
            ) from exc

        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY environment variable must be set for DeepSeek backend.")

        base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
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
