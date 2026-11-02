import os

from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.openrouter import OpenRouterProvider as _PydanticOpenRouterProvider


class OpenRouterProvider(object):

    def __init__(self, config):
        pass

    def get_llm_model(self, config, model_name):
        model = None
        for m in config.llm_providers.openrouter:
            if m.model == model_name:
                model = m
                break
        if model is None:
            raise Exception(f'{model_name} for OpenRouter provider not found in the config.yaml file')

        # Validate API key exists (pydantic-ai's OpenRouter provider reads OPENROUTER_API_KEY)
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY environment variable must be set")

        # Return a configured model object (not a bare string) so temperature and a
        # per-request timeout are actually applied — matching how the Anthropic provider
        # hardens its model. These settings are baked into the model, so both the planning
        # agent and the execution agent inherit them.
        temperature = float(getattr(model, "temp", 0.0) or 0.0)
        settings = OpenAIChatModelSettings(temperature=temperature, timeout=300.0)
        return OpenAIChatModel(
            model.model,
            provider=_PydanticOpenRouterProvider(api_key=api_key),
            settings=settings,
        )

    def get_agent_kwargs(self) -> dict:
        # Match the Anthropic provider: give pydantic-ai 3 output-validation retries
        # instead of the default 1, so a single malformed response doesn't fail the call.
        return {"output_retries": 3}
