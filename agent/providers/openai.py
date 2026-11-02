import os

from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.openai import OpenAIProvider as _PydanticOpenAIProvider


class OpenAIProvider(object):

    def __init__(self, config):
        pass

    def get_llm_model(self, config, model_name):
        model = None
        for m in config.llm_providers.openai:
            if m.model == model_name:
                model = m
                break
        if model is None:
            raise Exception(f'{model_name} for OpenAI provider not found in the config.yaml file')

        # Validate API key exists
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY environment variable must be set")

        # Return a configured model object (not a bare "openai:<model>" string) so the
        # temperature from config.yaml and a per-request timeout are actually applied —
        # matching how the OpenRouter/Anthropic providers harden their models. These
        # settings are baked into the model, so both the planning agent and the
        # execution agent inherit them.
        temperature = float(getattr(model, "temp", 0.0) or 0.0)
        settings = OpenAIChatModelSettings(temperature=temperature, timeout=300.0)
        return OpenAIChatModel(
            model.model,
            provider=_PydanticOpenAIProvider(api_key=api_key),
            settings=settings,
        )

    def get_agent_kwargs(self) -> dict:
        # Match the Anthropic/OpenRouter providers: give pydantic-ai 3 output-validation
        # retries instead of the default 1, so a single malformed response doesn't fail
        # the call.
        return {"output_retries": 3}
