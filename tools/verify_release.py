
#!/usr/bin/env python3
import argparse
import hashlib
import json
from pathlib import Path

base=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser()
p.add_argument("--manifest-only",action="store_true",help="Check paths without opening Git LFS large objects")
args=p.parse_args()
data=json.loads((base/"metadata/data-manifest.json").read_text(encoding="utf-8"))
missing=[];bad=[];ok=0
for entry in data["files"]:
    f=base/entry["path"]
    if not f.is_file():missing.append(entry["path"]);continue
    if not args.manifest_only:
        h=hashlib.sha256()
        with f.open("rb") as inp:
            for part in iter(lambda:inp.read(1024*1024),b""):h.update(part)
        if h.hexdigest()!=entry["sha256"]:bad.append(entry["path"]);continue
    ok+=1
print(f"verified={ok} missing={len(missing)} mismatched={len(bad)}")
for x in (missing+bad)[:15]:print("MISMATCH:",x)
raise SystemExit(0 if not missing and not bad else 1)
