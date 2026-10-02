"""Entry point for ``python -m culture_rules``."""

from __future__ import annotations

import sys

from culture_rules.cli import main

if __name__ == "__main__":
    sys.exit(main())
