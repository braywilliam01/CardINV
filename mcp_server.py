"""
Standalone MCP server exposing a CardINV user's collection and decks
as tools an AI agent can call — e.g. to suggest a new deck build from
what's actually available.

Runs as its own process, separate from the FastAPI app: it talks to a
running CardINV instance purely over its /api/agent/* HTTP endpoints
(the same surface any other agent framework would use), authenticated
with a personal API key issued via POST /api/auth/api-keys while
logged into the web UI.

Configuration (environment variables):
  CARDINV_BASE_URL   Base URL of the CardINV instance, e.g.
                      "http://localhost:8000" (no trailing slash).
  CARDINV_API_KEY    A token from POST /api/auth/api-keys, sent as
                      "Authorization: Bearer <token>".

Run directly (stdio transport, the standard local-launch mode for
Claude Desktop/Code):
  CARDINV_BASE_URL=http://localhost:8000 CARDINV_API_KEY=cardinv_... \\
      python mcp_server.py
"""

import os

import httpx
from mcp.server.fastmcp import FastMCP

CARDINV_BASE_URL = os.environ["CARDINV_BASE_URL"].rstrip("/")
CARDINV_API_KEY = os.environ["CARDINV_API_KEY"]

_client = httpx.Client(
    base_url=CARDINV_BASE_URL,
    headers={"Authorization": f"Bearer {CARDINV_API_KEY}"},
    timeout=30.0,
)

mcp = FastMCP("cardinv")


@mcp.tool()
def list_collection(game: str = "mtg") -> dict:
    """Every card the authenticated CardINV user owns, with quantity,
    printing/finish breakdown, how many are checked out to decks vs.
    available, and price where known. `game` is "mtg" or "pokemon"."""
    response = _client.get("/api/agent/collection", params={"game": game})
    response.raise_for_status()
    return response.json()


@mcp.tool()
def list_decks(game: str = "mtg") -> dict:
    """Every deck the authenticated CardINV user has already built,
    with its cards — use this alongside list_collection to avoid
    suggesting a duplicate of something that already exists. `game` is
    "mtg" or "pokemon"."""
    response = _client.get("/api/agent/decks", params={"game": game})
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    mcp.run(transport="stdio")
