"""The MCP server over the one command registry (needs the ``mcp`` extra for the server).

``tools`` is dependency-free (registry -> tool specs and dispatch); ``server`` lazily imports the
official ``mcp`` SDK. Everything reaches the engine through the HTTP API client only.
"""
