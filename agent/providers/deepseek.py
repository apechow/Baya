import os

class DeepSeekProvider(object):

    def __init__(self, config):
        pass

    def get_llm_model(self, config, model_name):
        model = None
        for m in config.llm_providers.deepseek:
            if m.model == model_name:
                model = m
                break
        if model is None:
            raise Exception(f'{model_name} for DeepSeek provider not found in the config.yaml file')

        # Validate API key exists (pydantic-ai's DeepSeek provider reads DEEPSEEK_API_KEY)
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY environment variable must be set")

        # Return pydantic-ai format string
        return f"deepseek:{model.model}"

    def get_agent_kwargs(self) -> dict:
        return {}
