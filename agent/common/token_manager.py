"""
Token management for MCP tool authentication.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from agent.common.configurator import Configurator


class TokenStore:
    """
    Centralized token storage and retrieval for MCP tool authentication.
    Tokens can be set from environment variables, login tools, or manually.
    """
    def __init__(self):
        self._tokens: Dict[str, Dict[str, str]] = {}  # {server_name: {token_type: token}}
        self._load_from_env()
    
    def _load_from_env(self):
        """Load tokens from environment variables if available."""
        # Check for common token environment variables
        customer_token = os.getenv("CUSTOMER_AUTH_TOKEN") or os.getenv("AUTH_TOKEN")
        admin_token = os.getenv("ADMIN_AUTH_TOKEN")
        gitlab_token = os.getenv("GRAPHQL_TOKEN")
        reddit_token = os.getenv("REDDIT_TOKEN")

        # IMPORTANT:
        # Tool calls look up tokens by MCP server name (e.g. "webarena" from config.yaml),
        # but older code stored tokens under "shopping". Register tokens under both names
        # for backward compatibility.
        server_aliases = ["webarena", "shopping", "gitlab", "reddit"]

        if customer_token:
            for server in server_aliases:
                self.set_token(server, "customer", customer_token)
        if admin_token:
            for server in server_aliases:
                self.set_token(server, "admin", admin_token)
        if gitlab_token:
            self.set_token("gitlab", "token", gitlab_token)
        if reddit_token:
            self.set_token("reddit", "token", reddit_token)
    
    def set_token(self, server_name: str, token_type: str, token: str):
        """Set a token for a specific server and type."""
        if server_name not in self._tokens:
            self._tokens[server_name] = {}
        self._tokens[server_name][token_type] = token
    
    def get_token(self, server_name: str, token_type: str = "customer") -> Optional[str]:
        """Get a token for a specific server and type."""
        return self._tokens.get(server_name, {}).get(token_type)
    
    def get_token_for_tool(self, server_name: str, tool_name: str) -> Optional[str]:
        """Get appropriate token based on tool name and server (admin vs customer vs generic)."""
        # Convert server name to lowercase for comparison
        server_lower = server_name.lower()
        tool_lower = tool_name.lower()

        # For GitLab, Reddit, and other servers, use the generic "token" type
        if server_lower in ["gitlab", "reddit"]:
            return self.get_token(server_name, "token")

        # For webarena/shopping, check if tool requires admin token
        if any(keyword in tool_lower for keyword in ['admin', 'create_product', 'update_product',
                'delete_product', 'create_category', 'update_customer', 'delete_customer',
                'create_invoice', 'create_shipment', 'cancel_order']):
            return self.get_token(server_name, "admin") or self.get_token(server_name, "customer")
        else:
            return self.get_token(server_name, "customer") or self.get_token(server_name, "admin")
    
    def has_token(self, server_name: str, token_type: str = "customer") -> bool:
        """Check if a token exists."""
        return self.get_token(server_name, token_type) is not None


# def _parse_token_from_response(response: Any) -> Optional[str]:
#     """
#     Parse token from login tool response.
#     Response can be a string (JSON), dict, or already contain the token.
#     The MCP client returns res.content[0].text which is a string.
#     """
#     if isinstance(response, str):
#         # Try to parse as JSON string first
#         try:
#             parsed = json.loads(response)
#             if isinstance(parsed, dict):
#                 # Check for "token" key
#                 token = parsed.get("token")
#                 if token:
#                     return token
#                 # Sometimes the response itself is the token
#                 if "error" not in parsed and "message" in parsed:
#                     # Might be a success message, check if token is elsewhere
#                     pass
#             # If parsed is a string, it might be the token itself
#             if isinstance(parsed, str) and len(parsed) > 20:  # Tokens are usually long
#                 return parsed
#         except (json.JSONDecodeError, TypeError):
#             # If parsing fails, check if it's a plain token string
#             # Tokens are usually long alphanumeric strings
#             if response and len(response) > 20 and not response.startswith("{"):
#                 return response
#     elif isinstance(response, dict):
#         return response.get("token")
#     return None


# def _update_env_file(env_path: Path, key: str, value: str):
#     """
#     Update or add a key-value pair in a .env or .server_env file.
#     Preserves other entries and comments.
#     """
#     env_path.parent.mkdir(parents=True, exist_ok=True)
    
#     # Read existing content
#     lines = []
#     key_found = False
    
#     if env_path.exists():
#         with open(env_path, 'r') as f:
#             lines = f.readlines()
    
#     # Update or add the key
#     new_lines = []
#     for line in lines:
#         stripped = line.strip()
#         # Check if this line contains our key (handle comments and whitespace)
#         if stripped.startswith(f"{key}=") or stripped.startswith(f"{key} ="):
#             new_lines.append(f"{key}={value}\n")
#             key_found = True
#         else:
#             new_lines.append(line)
    
#     # Add the key if it wasn't found
#     if not key_found:
#         new_lines.append(f"{key}={value}\n")
    
#     # Write back to file
#     with open(env_path, 'w') as f:
#         f.writelines(new_lines)


# async def authenticate_and_save_tokens(
#     tools: Dict[str, Any],  # ToolDefinition
#     token_store: TokenStore,
#     config: Configurator,
#     customer_username: Optional[str] = None,
#     customer_password: Optional[str] = None,
#     admin_username: Optional[str] = None,
#     admin_password: Optional[str] = None,
#     save_to_env: bool = True,
#     skip_customer: bool = False,
#     skip_admin: bool = False
# ) -> Dict[str, Optional[str]]:
#     """
#     Authenticate using login tools and save tokens to TokenStore and .server_env file.
    
#     Args:
#         tools: Dictionary of available tools
#         token_store: TokenStore instance to update
#         config: Configurator instance to get .server_env file path
#         customer_username: Customer login username (prompts if not provided)
#         customer_password: Customer login password (prompts if not provided)
#         admin_username: Admin login username (prompts if not provided)
#         admin_password: Admin login password (prompts if not provided)
#         save_to_env: Whether to save tokens to .server_env file (default: True)
    
#     Returns:
#         Dictionary with 'customer_token' and 'admin_token' (None if not obtained)
#     """
#     tokens = {"customer_token": None, "admin_token": None}
    
#     # Get .server_env file path from config (for server tokens)
#     env_path = Path(config.project_root) / config.get_key('server_env_path')
    
#     # Customer login (skip if skip_customer flag is set)
#     if not skip_customer:
#         customer_tool_name = None
#         for tool_name in tools.keys():
#             if "customer_login" in tool_name.lower():
#                 customer_tool_name = tool_name
#                 break
        
#         if customer_tool_name:
#             try:
#                 if not customer_username:
#                     customer_username = input("Enter customer username/email: ").strip()
#                 if not customer_password:
#                     import getpass
#                     customer_password = getpass.getpass("Enter customer password: ").strip()
                
#                 print("Authenticating as customer...")
#                 login_tool = tools[customer_tool_name]
#                 result = await login_tool.execute(credentials={
#                     "username": customer_username,
#                     "password": customer_password
#                 })
                
#                 token = _parse_token_from_response(result)
#                 if token:
#                     # Extract server name from tool name (e.g., "shopping-customer_login" -> "shopping")
#                     server_name = customer_tool_name.split("-")[0] if "-" in customer_tool_name else "shopping"
#                     token_store.set_token(server_name, "customer", token)
#                     tokens["customer_token"] = token
                    
#                     if save_to_env:
#                         _update_env_file(env_path, "CUSTOMER_AUTH_TOKEN", token)
#                         print(f"✓ Customer token saved to {env_path}")
#                     else:
#                         print("✓ Customer token stored in TokenStore")
#                 else:
#                     print(f"✗ Customer login failed: {result}")
#             except Exception as e:
#                 print(f"✗ Error during customer login: {e}")
    
#     # Admin login (skip if skip_admin flag is set)
#     if not skip_admin:
#         admin_tool_name = None
#         for tool_name in tools.keys():
#             if "admin_login" in tool_name.lower():
#                 admin_tool_name = tool_name
#                 break
        
#         if admin_tool_name:
#             try:
#                 if not admin_username:
#                     admin_username = input("Enter admin username: ").strip()
#                 if not admin_password:
#                     import getpass
#                     admin_password = getpass.getpass("Enter admin password: ").strip()
                
#                 print("Authenticating as admin...")
#                 login_tool = tools[admin_tool_name]
#                 result = await login_tool.execute(credentials={
#                     "username": admin_username,
#                     "password": admin_password
#                 })
                
#                 token = _parse_token_from_response(result)
#                 if token:
#                     # Extract server name from tool name
#                     server_name = admin_tool_name.split("-")[0] if "-" in admin_tool_name else "shopping"
#                     token_store.set_token(server_name, "admin", token)
#                     tokens["admin_token"] = token
                    
#                     if save_to_env:
#                         _update_env_file(env_path, "ADMIN_AUTH_TOKEN", token)
#                         print(f"✓ Admin token saved to {env_path}")
#                     else:
#                         print("✓ Admin token stored in TokenStore")
#                 else:
#                     print(f"✗ Admin login failed: {result}")
#             except Exception as e:
#                 print(f"✗ Error during admin login: {e}")
    
#     return tokens


# async def setup_authentication(tools: Dict[str, Any], token_store: TokenStore, config: Configurator):
#     """
#     Helper function to programmatically set authentication tokens via login tools.
#     This can be called after tool initialization to ensure tokens are set.
    
#     This function will prompt for credentials and save tokens to both TokenStore and .server_env file.
#     """
#     await authenticate_and_save_tokens(
#         tools=tools,
#         token_store=token_store,
#         config=config,
#         save_to_env=True
#     )
