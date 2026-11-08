#!/usr/bin/env python3
"""Verify ZIP CRC and the internal SHA-256 manifest for a Phase 7 result bundle."""
from __future__ import annotations

import hashlib
import pathlib
import sys
import zipfile


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_phase7_bundle.py RESULT_BUNDLE.zip", file=sys.stderr)
        return 2
    bundle = pathlib.Path(sys.argv[1])
    if not bundle.is_file():
        print(f"bundle not found: {bundle}", file=sys.stderr)
        return 2
    with zipfile.ZipFile(bundle) as archive:
        corrupt = archive.testzip()
        print("ZIP_TEST=" + ("OK" if corrupt is None else str(corrupt)))
        if corrupt is not None:
            return 3
        try:
            checksum_text = archive.read("SHA256SUMS.txt").decode("utf-8-sig")
        except KeyError:
            print("SHA256SUMS.txt missing", file=sys.stderr)
            return 4
        rows = [line for line in checksum_text.splitlines() if line.strip()]
        if not rows:
            print("SHA256SUMS.txt is empty", file=sys.stderr)
            return 5
        for row in rows:
            try:
                expected, relative_path = row.split("  ", 1)
            except ValueError:
                print(f"invalid checksum row: {row!r}", file=sys.stderr)
                return 6
            try:
                payload = archive.read(relative_path)
            except KeyError:
                print(f"checksum target missing: {relative_path}", file=sys.stderr)
                return 7
            actual = hashlib.sha256(payload).hexdigest()
            if actual.lower() != expected.lower():
                print(f"SHA256_MISMATCH={relative_path}", file=sys.stderr)
                return 8
        print(f"INTERNAL_SHA256={len(rows)}/{len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
