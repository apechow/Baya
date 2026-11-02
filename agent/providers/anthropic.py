import os
from typing import Any

from anthropic import NOT_GIVEN, APIStatusError
from anthropic import omit as OMIT
from pydantic_ai import ModelHTTPError
from pydantic_ai.exceptions import UserError
from pydantic_ai.models import get_user_agent
from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings


class _CachedAnthropicModel(AnthropicModel):
    """AnthropicModel that injects cache_control on the system prompt and the first
    user text block, enabling Anthropic prompt caching for large planning prompts."""

    async def _messages_create(self, messages, stream, model_settings, model_request_parameters):
        tools = self._get_tools(model_request_parameters)
        tools, mcp_servers, beta_features = self._add_builtin_tools(tools, model_request_parameters)

        tool_choice = None
        if tools:
            if not model_request_parameters.allow_text_output:
                tool_choice = {"type": "any"}
                if (
                    (thinking := model_settings.get("anthropic_thinking"))
                    and thinking.get("type") == "enabled"
                ):
                    raise UserError(
                        "Anthropic does not support thinking and output tools at the same time. "
                        "Use `output_type=PromptedOutput(...)` instead."
                    )
            else:
                tool_choice = {"type": "auto"}

            if (allow_parallel := model_settings.get("parallel_tool_calls")) is not None:
                tool_choice["disable_parallel_tool_use"] = not allow_parallel

        system_prompt, anthropic_messages = await self._map_message(messages)

        # Wrap system prompt as a cached content block list.
        if system_prompt:
            system_param: Any = [
                {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
            ]
        else:
            system_param = OMIT

        # Add cache_control to the first text block in the last user message.
        # This caches the large static prompt portion (e.g. the endpoints list).
        if anthropic_messages:
            last_msg = anthropic_messages[-1]
            content = last_msg.get("content") if isinstance(last_msg, dict) else getattr(last_msg, "content", None)
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text" and "cache_control" not in block:
                        block["cache_control"] = {"type": "ephemeral"}
                        break

        try:
            extra_headers = dict(model_settings.get("extra_headers") or {})
            extra_headers.setdefault("User-Agent", get_user_agent())
            if beta_features:
                if "anthropic-beta" in extra_headers:
                    beta_features.insert(0, extra_headers["anthropic-beta"])
                extra_headers["anthropic-beta"] = ",".join(beta_features)

            return await self.client.beta.messages.create(
                max_tokens=model_settings.get("max_tokens", 4096),
                system=system_param,
                messages=anthropic_messages,
                model=self._model_name,
                tools=tools or OMIT,
                tool_choice=tool_choice or OMIT,
                mcp_servers=mcp_servers or OMIT,
                stream=stream,
                thinking=model_settings.get("anthropic_thinking", OMIT),
                stop_sequences=model_settings.get("stop_sequences", OMIT),
                temperature=model_settings.get("temperature", OMIT),
                top_p=model_settings.get("top_p", OMIT),
                timeout=model_settings.get("timeout", NOT_GIVEN),
                metadata=model_settings.get("anthropic_metadata", OMIT),
                extra_headers=extra_headers,
                extra_body=model_settings.get("extra_body"),
            )
        except APIStatusError as e:
            if (status_code := e.status_code) >= 400:
                raise ModelHTTPError(status_code=status_code, model_name=self.model_name, body=e.body) from e
            raise


class AnthropicProvider(object):

    def __init__(self, config):
        pass

    def get_llm_model(self, config, model_name):
        model = None
        for m in config.llm_providers.anthropic:
            if m.model == model_name:
                model = m
                break
        if model is None:
            raise Exception(f'{model_name} for Anthropic provider not found in the config.yaml file')

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY environment variable must be set")

        return _CachedAnthropicModel(model.model)

    def get_agent_kwargs(self) -> dict:
        return {"output_retries": 3}
