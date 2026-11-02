import os
from pydantic_ai import models

class GoogleProvider(object):

    def __init__(self, config):
        pass

    def get_llm_model(self, config, model_name):
        model = None
        for m in config.llm_providers.google:
            if m.model == model_name:
                model = m
                break
        if model is None:
            raise Exception(f'{model_name} for Google provider not found in the config.yaml file')
        
        # Use direct Gemini API instead of Vertex AI
        # pydantic-ai uses GOOGLE_API_KEY, but also check GEMINI_API_KEY for backwards compatibility
        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY or GEMINI_API_KEY environment variable must be set")

        return f"google-gla:{model.model}"

    def get_agent_kwargs(self) -> dict:
        return {}
