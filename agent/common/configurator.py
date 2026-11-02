#
# Copyright 2025 Project Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import os
import yaml
from dotenv import load_dotenv
from urllib.parse import urlparse
from types import SimpleNamespace
from pathlib import Path

class Configurator:
    def __init__(self):
        current_file = os.path.abspath(__file__)
        self.project_root = os.path.abspath(os.path.join(os.path.dirname(current_file), '..', '..'))
        config_path = os.path.join(self.project_root, 'config', 'config.yaml')
        
        self.path = config_path
        self._data = None
        self.load()
        self.data = self.dict_to_namespace(self._data)
        self.d = None
        self.active_mcp_servers = []

    def verify_root_path(self, p):
        file_path = Path(p)
        file_path.parent.mkdir(parents=True, exist_ok=True)

    def get_app_tree_path(self, app):
        #current_file = os.path.abspath(__file__)
        #self.project_root = os.path.abspath(os.path.join(os.path.dirname(current_file), '..', '..'))
        if app in self.data.app_list:
            return os.path.join(self.project_root, self.data.scope_hierarchy.tree.format(APP = app))
        raise Exception(f"App '{app}' not found in app_list")

    def get_app_mapping_path(self, app):
        #current_file = os.path.abspath(__file__)
        #self.project_root = os.path.abspath(os.path.join(os.path.dirname(current_file), '..', '..'))
        if app in self.data.app_list:
            return os.path.join(self.project_root, self.data.scope_hierarchy.mapping.format(APP = app))
        raise Exception(f"App '{app}' not found in app_list")

    def get_app_reverse_mapping_path(self, app):
        #current_file = os.path.abspath(__file__)
        #self.project_root = os.path.abspath(os.path.join(os.path.dirname(current_file), '..', '..'))
        if app in self.data.app_list:
            return os.path.join(self.project_root, self.data.mcp_reverse_mapping.format(APP = app))
        raise Exception(f"App '{app}' not found in app_list")

    def get_eval_prompts(self, path_type, app1, app2 = None, provider = 'openai', model = ''):
        # Options for path type: single_app_multi_methods, single_app_single_methods, multi_app_multi_methods
        # And real_accounts_single, real_accounts_multiple, and real_accounts_cross_app
        #current_file = os.path.abspath(__file__)
        #self.project_root = os.path.abspath(os.path.join(os.path.dirname(current_file), '..', '..'))
        apps_path = os.path.join(self.project_root, 'miniscope')
        prompt_file = self.get_key(path_type, "eval_prompts")
        prompt_file = prompt_file.format(MODEL = model, PROVIDER = provider, APP1 = app1, APP2 = app2)
        return os.path.join(self.project_root, prompt_file)
    
    def get_suite_apps(self, suite):
        return self._data['suites'][suite]['apps']
    
    def get_llm_solution(self, path_type, app, provider = None, model = ''):
        # Options for path type: single_app_multi_methods, single_app_single_methods, multi_app_multi_methods
        #current_file = os.path.abspath(__file__)
        #self.project_root = os.path.abspath(os.path.join(os.path.dirname(current_file), '..', '..'))
        apps_path = os.path.join(self.project_root, 'miniscope')
        solution_file = self.get_key(path_type, "llm_solution")
        solution_file = solution_file.format(MODEL = model, PROVIDER = provider, APP1 = app)
        return os.path.join(self.project_root, solution_file)  

    def get_miniscope_solution(self, path_type, app):
        # Options for path type: single_app_multi_methods, single_app_single_methods, multi_app_multi_methods
        #current_file = os.path.abspath(__file__)
        #self.project_root = os.path.abspath(os.path.join(os.path.dirname(current_file), '..', '..'))
        apps_path = os.path.join(self.project_root, 'miniscope')
        solution_file = self.get_key(path_type, "miniscope_solution")
        solution_file = solution_file.format(APP1 = app)
        return os.path.join(self.project_root, solution_file)  

    def get_apps_list(self):
        return self._data['app_list']
    
    def get_suites_list(self):
        return list(self._data['suites'].keys())

    def get_providers_list(self):
        res = []
        for p in self._data['llm_providers']:
            res.append(str(p))
        return res

    def get_accounts_list(self, app):
        res = []
        if app in self._data['test_accounts']:
            for p in self._data['test_accounts'][app]:
                name = p['name']
                email = p['email']
                res.append(f"{name} <{email}>")
                if 'user_id' in p:
                    user_id = p['user_id']
                    res.append(f" user ID: {user_id}")
        return res

    def get_supported_models(self, provider):
        res = []
        for p in self._data['llm_providers'][provider]:
            res.append(p['model'])
        return res

    def load(self):
        if self._data is None:
            with open(self.path, "r") as f:
                self._data = yaml.safe_load(f)
        return self._data

    def dict_to_namespace(self,d):
        if isinstance(d, dict):
            return SimpleNamespace(**{k: self.dict_to_namespace(v) for k, v in d.items()})
        elif isinstance(d, list):
            return [self.dict_to_namespace(i) for i in d]
        return d

    def get_key(self, key, section = None):
        if section is not None and section in self._data:
            section = self._data[section]
        else:
            section = self._data
            
        if key in section.keys():
            return section[key]
        return None

    def get_mcp_servers(self):
        servers = []
        for mcp_server in self.get_key('mcp_server'):
            self.active_mcp_servers.append(mcp_server)
            servers.append({'name': mcp_server, 'url': self.get_key(mcp_server, 'mcp_server')})
        # print("here are the servers: " + str(servers))
        return servers

    def load_client_env(self):
        p = os.path.join(self.project_root, self.get_key('client_env_path'))
        # override=True so config/.env is the single source of truth: values here
        # always win over anything already exported in the shell (e.g. a stale
        # OPENAI_API_KEY in ~/.zshrc / ~/.bashrc), which load_dotenv otherwise
        # leaves untouched (override defaults to False).
        load_dotenv(dotenv_path=p, override=True)

    def load_mcpserver_env(self):
        p = os.path.join(self.project_root, self.get_key('mcp_env_path'))
        load_dotenv(dotenv_path=p)

    def load_shared_env(self):
        p = os.path.join(self.project_root, self.get_key('shared_env_path'))
        load_dotenv(dotenv_path=p)

    def load_server_env(self):
        p = os.path.join(self.project_root, self.get_key('server_env_path'))
        load_dotenv(dotenv_path=p)

    def load_all_env(self):
        """Load all environment files at once (client, shared, and server)."""
        self.load_client_env()
        self.load_shared_env()
        self.load_server_env()

    def get_hostname_port(self, url):
        parsed = urlparse(url)
        return parsed.hostname, parsed.port, parsed.path
    
    def check_llm_env_vars(self):
        llm_keys = {
            "OPENAI_API_KEY": "OpenAI",
            "ANTHROPIC_API_KEY": "Anthropic",
            "GOOGLE_API_KEY": "Google",
            "GEMINI_API_KEY": "Google Generative AI (Gemini)",
            "DEEPSEEK_API_KEY": "DeepSeek",
            "OPENROUTER_API_KEY": "OpenRouter",
            # "AZURE_OPENAI_API_KEY": "Azure OpenAI",
            # "MISTRAL_API_KEY": "Mistral",
            # "COHERE_API_KEY": "Cohere",
            # "PALM_API_KEY": "Google PaLM",
            # "HF_API_KEY": "HuggingFace",
            # "TOGETHER_API_KEY": "Together AI",
            # "OPENROUTER_API_KEY": "OpenRouter",
        }

        found = False
        print("🔍 Checking for LLM-related environment variables:")
        for key, name in llm_keys.items():
            value = os.getenv(key)
            if value and len(value) >= 8:
                masked = f"{value[:4]}***{value[-4:]}"
                print(f"✅ {name} ({key}): {masked}")
                found = True
            elif value:
                print(f"✅ {name}: {value} (not masked, too short)")
                found = True

        if not found:
            print("⚠️  No common LLM API keys found in the environment.")

        print("\n")
