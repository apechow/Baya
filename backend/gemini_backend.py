"""
Gemini-backed ChatModel implementation.
"""

import os
from typing import Sequence

from agent.common.types import ChatMessage, ChatModel


class GeminiChatModel(ChatModel):
    """ChatModel implementation using Google Gemini via google-generativeai."""

    def __init__(self, model: str | None = None) -> None:
        try:
            import google.generativeai as genai  # type: ignore
        except ImportError as exc:  # pragma: no cover - import guard
            raise RuntimeError(
                "Google Generative AI client library not installed. Install `google-generativeai` to use this backend."
            ) from exc

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY environment variable must be set for Gemini backend.")

        genai.configure(api_key=api_key)
        self._genai = genai
        self.model_name = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    def complete(self, messages: Sequence[ChatMessage]) -> str:
        system_instruction = None
        prompt_lines = []
        for message in messages:
            if message.role == "system" and system_instruction is None:
                system_instruction = message.content
            else:
                prompt_lines.append(f"{message.role.upper()}: {message.content}")

        prompt = "\n\n".join(prompt_lines)
        model = self._genai.GenerativeModel(self.model_name, system_instruction=system_instruction)
        response = model.generate_content(prompt)
        if hasattr(response, "text") and response.text:
            return response.text
        # Fallback for older SDK signatures
        if response and getattr(response, "candidates", None):
            for candidate in response.candidates:
                if candidate.content and candidate.content.parts:
                    return "".join(part.text for part in candidate.content.parts if getattr(part, "text", None))
        return ""
