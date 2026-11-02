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

import os
import logging
from agent.providers.provider import ModelProvider


def get_mcp_logger(name = 'mcp.log'):
    log_path = os.path.expanduser(f"./logs/{name}")
    log_dir = os.path.dirname(log_path)
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
        
    logging.basicConfig(filename=log_path, level=logging.DEBUG, force=True)
    logger = logging.getLogger("mcp.server.lowlevel.server")
    
    return logger