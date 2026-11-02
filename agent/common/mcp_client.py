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

from fastmcp import Client
import aiohttp
from fastmcp.client.transports import StreamableHttpTransport

async def call_tool_with_token(mpcserver: str, token: str, method: str, params: {}):
    headers = {}
    if token:
        headers = {"Authorization": f"Bearer {token}"}
    transport_explicit = StreamableHttpTransport(url=mpcserver, headers=headers)
    client = Client(transport_explicit)

    async with client:
        res = await client.call_tool(method, params)
        # print(f"*****Connected via Streamable HTTP, result: {res}")
        return res.content[0].text

async def is_server_alive(mcpserver: str):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(mcpserver) as resp:
                return True
    except Exception as e:
        print(f"{mcpserver} is not available")
        print(f"Error: {e}")
        return False

async def list_tools(mcpserver: str):
    # print(f"Listing tools from MCP server: {mcpserver}")
    tools = []
    if await is_server_alive(mcpserver):
        # print(f"{mcpserver} is alive, attempting to list tools...")
        async with Client(mcpserver) as client:
            try:
                result = await client.list_tools_mcp()
                # print(f"Tools retrieved from {mcpserver}: {result.tools}")
                tools = result.tools
                for t in tools:
                    t.server = mcpserver
            except Exception as e:
                print(f"[{mcpserver}] Error during tool listing: {e}")
    return tools

# async def get_all_tools(config):

#     tools = {}
#     for mcp in config.get_mcp_servers():
#         ltools = []
#         res = await list_tools(mcp['url'])
#         if res is not None:
#             ltools.append(res)
#         tools[mcp['name']] = [item for sublist in ltools for item in sublist]
#     return tools

# async def render_mcp_methods(mcp_tools_dict, app_name):
#     methods = mcp_tools_dict.get(app_name, [])
#     rendered = []

#     for tool in methods:
#         method_name = tool.name
#         description = tool.description
#         input_schema = tool.inputSchema
#         arg_keys = input_schema.get('properties', {}).keys()
#         required_args = input_schema.get('required', [])

#         # Filter out 'ctx' from args unless you want it explicitly visible to the LLM
#         arg_keys_filtered = [arg for arg in arg_keys if arg != "ctx"]

#         rendered.append(
#             f"- **{method_name}**: {description}\n"
#             f"  - Required args: {', '.join(required_args) if required_args else 'None'}\n"
#             f"  - All args: {', '.join(arg_keys_filtered)}"
#         )

#     return "\n\n".join(rendered)

