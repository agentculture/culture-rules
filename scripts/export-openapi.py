#!/usr/bin/env python3
"""Write (--write) or verify (--check) the committed HTTP contract ``api/openapi.json``.

The file is the single contract consumed by the web types, the CLI/MCP API client and the
parity test: any route or schema change must update it in the same PR. Needs the ``server``
extra (``uv sync`` includes it in the dev group).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from culture_rules.server.contract import CONTRACT_PATH, build_openapi, render_openapi  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="regenerate api/openapi.json")
    mode.add_argument("--check", action="store_true", help="fail if the file has drifted")
    args = parser.parse_args(argv)
    target = ROOT / CONTRACT_PATH
    text = render_openapi(build_openapi())
    if args.write:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print(f"wrote {CONTRACT_PATH}")
        return 0
    current = target.read_text(encoding="utf-8") if target.exists() else ""
    if current != text:
        print(f"{CONTRACT_PATH} is out of date; run: python scripts/export-openapi.py --write")
        return 1
    print(f"{CONTRACT_PATH} is up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
