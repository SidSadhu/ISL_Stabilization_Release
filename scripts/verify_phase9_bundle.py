from __future__ import annotations
import hashlib
import pathlib
import sys
import zipfile

if len(sys.argv) != 2:
    raise SystemExit("usage: verify_phase9_bundle.py <bundle.zip>")
path = pathlib.Path(sys.argv[1])
with zipfile.ZipFile(path) as archive:
    bad = archive.testzip()
    print("ZIP_TEST=" + ("OK" if bad is None else str(bad)))
    if bad is not None:
        raise SystemExit(3)
    sums = archive.read("SHA256SUMS.txt").decode("utf-8-sig").splitlines()
    checked = 0
    for line in sums:
        if not line.strip():
            continue
        digest, relative = line.split("  ", 1)
        actual = hashlib.sha256(archive.read(relative)).hexdigest()
        if actual.lower() != digest.lower():
            raise SystemExit("SHA256_MISMATCH=" + relative)
        checked += 1
    print(f"INTERNAL_SHA256={checked}/{checked}")
