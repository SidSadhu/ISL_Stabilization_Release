from __future__ import annotations
import sys
if sys.version_info[:2] != (3, 12):
    print(f"EXPECTED=3.12 ACTUAL={sys.version_info.major}.{sys.version_info.minor}")
    raise SystemExit(2)
print(f"{sys.version_info.major}.{sys.version_info.minor}")
