"""
Backend chat model registry for PlanThenExecuteAgent.
"""

from typing import Callable, Dict

from agent.common.types import ChatModel
from .anthropic_backend import AnthropicChatModel
from .deepseek_backend import DeepSeekChatModel
from .gemini_backend import GeminiChatModel
from .openai_backend import OpenAIChatModel


_REGISTRY: Dict[str, Callable[[], ChatModel]] = {
    "openai": OpenAIChatModel,
    "anthropic": AnthropicChatModel,
    "google": GeminiChatModel,
    "deepseek": DeepSeekChatModel,
}


def get_chat_model(name: str) -> ChatModel:
    """Return a chat model instance for the given backend name."""
    key = name.lower()
    factory = _REGISTRY.get(key)
    if factory is None:
        available = ", ".join(sorted(_REGISTRY.keys()))
        raise ValueError(f"Unknown backend '{name}'. Available options: {available}")
    return factory()
