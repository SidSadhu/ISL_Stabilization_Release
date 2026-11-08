from __future__ import annotations
import subprocess
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit("usage: phase9_ffprobe_preflight.py <ffprobe.exe>")
path = Path(sys.argv[1]).expanduser().resolve()
if not path.is_file():
    raise SystemExit(f"FFPROBE_NOT_FOUND={path}")
result = subprocess.run([str(path), "-version"], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
combined = (result.stdout + "\n" + result.stderr).strip()
if result.returncode != 0:
    print(combined[-2000:])
    raise SystemExit(f"FFPROBE_EXIT={result.returncode}")
line = next((line for line in combined.splitlines() if line.startswith("ffprobe version ")), None)
if not line:
    print(combined[-2000:])
    raise SystemExit("FFPROBE_VERSION_LINE_MISSING")
print(line)
