"""``python -m culture_rules.mcp`` — serve the registry as MCP tools over stdio."""

from __future__ import annotations

import sys

from culture_rules.mcp import server


def main() -> int:
    try:
        server.run_stdio()
    except server.ServerExtraMissing as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
