"""
Tool definition and initialization for MCP tools.
"""

from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from agent.common.configurator import Configurator
from agent.common.mcp_client import call_tool_with_token, list_tools
from agent.common.token_manager import TokenStore


class ToolDefinition(BaseModel):
    """Pydantic model for tool definition"""
    name: str = Field(description="Full tool name (server_tool_name)")
    original_name: str = Field(description="Original tool name without server prefix")
    server: str = Field(description="MCP server name")
    description: str = Field(description="Tool description")
    input_schema: Dict[str, Any] = Field(description="Input schema for the tool")
    execute: Callable = Field(description="Async function to execute the tool")

    class Config:
        arbitrary_types_allowed = True


def build_pydantic_tools_from_mcp(
    tools_specs, 
    token_store: TokenStore, 
    server_name: str, 
    server_url: str
) -> List[ToolDefinition]:
    """
    Build Pydantic-friendly tool definitions from MCP tool specifications.

    Args:
        tools_specs: List of tool specifications from MCP server
        token_store: TokenStore instance for retrieving authentication tokens
        server_name: Name of the MCP server
        server_url: URL of the MCP server (for making tool calls)

    Returns:
        List of ToolDefinition objects
    """
    tools = []

    for spec in tools_specs:
        original_name = spec.name
        server = spec.server
        description = spec.description
        full_name = f"{server_name}-{spec.name}"  
        input_schema = spec.inputSchema if hasattr(spec, 'inputSchema') else {}

        # Create async execution function with closure that retrieves token dynamically
        def make_execute(tool_name: str, server_url: str, server_name: str, token_store: TokenStore, schema: Dict[str, Any]):
            async def execute(**kwargs):
                # Get appropriate token for this tool
                token = token_store.get_token_for_tool(server_name, tool_name)
                if not token:
                    # Fallback: try to get any available token
                    token = token_store.get_token(server_name, "customer") or token_store.get_token(server_name, "admin") or token_store.get_token(server_name, "token")

                # If the tool expects a 'token' parameter and we have a token, add it to kwargs
                if token and 'token' in (schema.get('properties', {}).keys() if schema else []):
                    kwargs['token'] = token

                # Use server_url (not server_name) for the actual MCP call
                result = await call_tool_with_token(server_url, token or "", tool_name, kwargs)
                return result
            return execute

        tool_def = ToolDefinition(
            name=full_name,
            original_name=original_name,
            server=server,
            description=description,
            input_schema=input_schema,
            execute=make_execute(original_name, server_url, server_name, token_store, input_schema)
        )

        tools.append(tool_def)

    return tools


async def initialize_tools(
    config: Configurator, 
    token_store: Optional[TokenStore] = None
) -> Tuple[Dict[str, ToolDefinition], TokenStore]:
    """
    Initialize tools from MCP servers in a Pydantic-friendly way.

    Args:
        config: Configurator instance with MCP server configurations
        token_store: Optional TokenStore instance (creates new one if not provided)

    Returns:
        Tuple of (tools dictionary, token_store instance)
    """
    if token_store is None:
        from agent.common.token_manager import TokenStore
        token_store = TokenStore()
    
    mcp_servers = config.get_mcp_servers()
    print(f"Initializing tools from {len(mcp_servers)} MCP servers...")

    tools_dict = {}

    for mcp in mcp_servers:
        server_name = mcp['name']
        server_url = mcp['url']

        try:
            # List tools from the MCP server
            res = await list_tools(server_url)

            if res is not None:
                # Build Pydantic-friendly tools with token store and server URL
                tool_definitions = build_pydantic_tools_from_mcp(res, token_store, server_name, server_url)

                # Add to dictionary
                for tool_def in tool_definitions:
                    tools_dict[tool_def.name] = tool_def

                # Check if tokens are available
                has_customer = token_store.has_token(server_name, "customer")
                has_admin = token_store.has_token(server_name, "admin")
                has_token = token_store.has_token(server_name, "token")
                token_status = []
                if has_customer:
                    token_status.append("customer")
                if has_admin:
                    token_status.append("admin")
                if has_token:
                    token_status.append("token")

                # Push token into the MCP server process via its set_token tool.
                # Servers like gitlab use a global auth dict populated at startup from
                # env vars. If the server process doesn't have the env var set, the
                # HTTP Authorization header the agent sends is ignored. Calling the
                # set_token tool is the only way to reliably inject the token.
                if has_token:
                    set_token_tool_name = f"{server_name}-{server_name}_set_token"
                    if set_token_tool_name not in tools_dict:
                        # Try common naming patterns
                        for candidate in [
                            f"{server_name}-{server_name}_set_token",
                            f"{server_name}-gitlab_set_token",
                            f"{server_name}-set_token",
                        ]:
                            if candidate in tools_dict:
                                set_token_tool_name = candidate
                                break
                    if set_token_tool_name in tools_dict:
                        try:
                            token_val = token_store.get_token(server_name, "token")
                            await tools_dict[set_token_tool_name].execute(token=token_val)
                            token_status.append("(pushed to server)")
                        except Exception as e:
                            print(f"  ⚠ Could not push token to {server_name}: {e}")

                status_msg = f"  ✓ Loaded {len(tool_definitions)} tools from {server_name}"
                if token_status:
                    status_msg += f" (tokens: {', '.join(token_status)})"
                else:
                    status_msg += " (no tokens set - will use server-side auth if available)"
                print(status_msg)
            else:
                print(f"  ✗ Failed to load tools from {server_name}")

        except Exception as e:
            print(f"  ✗ Error loading tools from {server_name}: {e}")

    print(f"Total tools available: {len(tools_dict)}")
    return tools_dict, token_store
