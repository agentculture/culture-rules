"""The one place the API's default address lives (culture-rules#141).

The server binds ``DEFAULT_HOST:DEFAULT_PORT`` when ``--host``/``--port`` and
``CULTURE_RULES_HOST``/``CULTURE_RULES_PORT`` are unset; the CLI, the HTTP client and the MCP
server talk to ``DEFAULT_API_URL`` when ``--api-url`` and ``CULTURE_RULES_API_URL`` are unset;
the web dev proxy (``web/vite.config.ts``) uses the same URL, and a test keeps it in step.

8791 is the port production's LAN listener already uses (production binds its LAN address,
not loopback, so the default URL does not reach it: set ``CULTURE_RULES_API_URL``). It is
not 8765: on spark that port belongs to the culture console, and dev pages left running with
the old default polled it until its abuse alerts fired.
"""

from __future__ import annotations

__all__ = ["DEFAULT_API_URL", "DEFAULT_HOST", "DEFAULT_PORT"]

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8791
# Plain HTTP on purpose: a loopback address; remote APIs are given explicitly (https).
DEFAULT_API_URL = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"  # NOSONAR
