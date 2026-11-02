"""
Shared type definitions for the agent system.
"""

from typing import Literal, Protocol, Sequence
from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """Minimal chat message schema for LLM clients."""

    role: Literal["system", "user", "assistant"]
    content: str


class ChatModel(Protocol):
    """Simple protocol an LLM client must satisfy."""

    def complete(self, messages: Sequence[ChatMessage]) -> str: ...

