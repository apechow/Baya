
import importlib

MODEL_PROVIDERS = {"openai" : "OpenAIProvider",
                   "anthropic" : "AnthropicProvider",
                   "google" : "GoogleProvider",
                   "google-gla" : "GoogleProvider",
                   "deepseek" : "DeepSeekProvider",
                   "openrouter" : "OpenRouterProvider"}
DEFAULT_PROVIDER  = "openai"

class ModelProvider(object):
    def __init__(self, config, model_provider = None, model_name = None):
        self.config = config.data
        if model_provider:
            self.llm_provider = model_provider
        else:
            self.llm_provider = self.config.agent_llm_provider.lower()

        if model_name:
            self.model_name = model_name
        else:
            self.model_name = self.config.agent_llm_model.lower()

    def get_llm_model_provider(self):
        prov = self.__import_provider_class(self.llm_provider)
        return prov.get_llm_model(self.config, self.model_name)

    def get_agent_kwargs(self) -> dict:
        prov = self.__import_provider_class(self.llm_provider)
        return prov.get_agent_kwargs() if hasattr(prov, "get_agent_kwargs") else {}

    def __import_provider_class(self, provider):
        
        if provider not in MODEL_PROVIDERS:
            raise ValueError(
                    f"Unsupported model provider: {provider}"
                )
        # Map provider names with hyphens to their module filename (no hyphens).
        MODULE_NAMES = {"google-gla": "google"}
        module_name = MODULE_NAMES.get(provider, provider)
        try:
            module = importlib.import_module(f'agent.providers.{module_name}')
            provider_class = getattr(module, MODEL_PROVIDERS.get(provider, DEFAULT_PROVIDER) )
            provider_instance = provider_class(self.config)
            return provider_instance
        except ImportError as e:
            raise (e)
