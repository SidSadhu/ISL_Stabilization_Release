from __future__ import annotations
import hashlib, pathlib, sys, zipfile
p=pathlib.Path(sys.argv[1])
with zipfile.ZipFile(p) as z:
    bad=z.testzip()
    print('ZIP_TEST=' + ('OK' if bad is None else str(bad)))
    if bad is not None: raise SystemExit(3)
    sums=z.read('SHA256SUMS.txt').decode('utf-8-sig').splitlines()
    checked=0
    for line in sums:
        if not line.strip(): continue
        digest, rel=line.split('  ',1)
        actual=hashlib.sha256(z.read(rel)).hexdigest()
        if actual.lower()!=digest.lower(): raise SystemExit('SHA256_MISMATCH='+rel)
        checked += 1
    print(f'INTERNAL_SHA256={checked}/{checked}')
