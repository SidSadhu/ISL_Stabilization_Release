#!/usr/bin/env python3
"""Emit the project interpreter major.minor version for PowerShell preflight."""
from __future__ import annotations

import sys


def main() -> int:
    print(f"{sys.version_info.major}.{sys.version_info.minor}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
