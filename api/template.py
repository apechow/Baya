#!/usr/bin/env python3
"""
Example API module.

This file doubles as living documentation for contributors who want to expose a
new website API to the agent:

1. Duplicate this file, rename it (e.g., `tickets.py`), and add the new file to
   `api/index.json` with a short description.
2. Keep the general layout: configuration + shared classes + async SDK endpoints.

When implemented, each endpoint with a @mcp.tool() decorator will be exposed to the agent.
Each of these endpoints needs a detailed docstring, and needs to be strongly typed.

Any API function that returns a value needs that value to be one of the following:
* Atomic data type
* Classes extending BaseModel where each attribute follows the same rules
* Lists of elements with the above rules

In particular, do not use dicts or other untyped structures as return values, as these hide the underlying data structure.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastmcp import FastMCP
from pydantic import BaseModel, Field, HttpUrl

# Give the server a descriptive name; this string shows up in logs/prompts.
mcp = FastMCP("Website Name")x

# ---------------------------------------------------------------------------
# SERVER CONFIGURATION
# ---------------------------------------------------------------------------


class ExampleClass(BaseModel):
    """Product data structure"""

    sku: str = Field(description="Product SKU")
    name: str = Field(description="Product name")
    price: float = Field(description="Product price")
    status: int = Field(default=1, description="Product status (1=enabled)")
    visibility: int = Field(default=4, description="Product visibility")
    type_id: str = Field(default="simple", description="Product type")
    attribute_set_id: int = Field(default=4, description="Attribute set ID")
    weight: Optional[float] = Field(default=1.0, description="Product weight")


# ============================================================================
# SDK
# ============================================================================


@mcp.tool()
async def search_articles(search_term: str) -> List[ExampleClass]:
    """
    Search the website for products based on a search term and return all search results.

    Args:
        search_term (str): The term to search for products.

    Returns:
        results (List[ExampleClass]): The search results containing a list of products.
    """

    # Example implementation: return products that include the search term in the name
    return [
        ExampleClass(
            sku="EX123", name=f"Example Product - {search_term}", price=19.99
        )
    ]
