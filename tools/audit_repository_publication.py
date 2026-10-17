#!/usr/bin/env python3
"""Read-only audit of tracked Markdown links and high-confidence secret shapes.

Does not inspect Git history, third-party reuse rights, real network endpoints
or users' gitignored browser profiles. Never prints matched secret contents.
"""
from __future__ import annotations
import argparse
from collections import Counter
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

ROOT=Path(__file__).resolve().parents[1]
LINK=re.compile(r"(?<!!)\[[^\]\n]{1,250}\]\(([^)\n]+)\)")
FENCE=re.compile(r"^\s*(?:\x60{3,}|~{3,})")
TEXT_EXT={".py",".js",".mjs",".json",".yaml",".yml",".toml",".ini",".sh",".ps1",
          ".psm1",".cmd",".bat",".md",".txt",".xml",".html",".command",".conf",".env"}
PRIVATE_NAMES={"config.local.json","sellersprite_cookies.json","id_rsa","secrets.json",
               "credentials.json",".env"}
PRIVATE_EXT={".pem",".p12",".pfx",".har",".pcap",".pcapng",".key"}
SECRET_REGEX={
    "github_token":re.compile(r"\b(?:github_pat_[A-Za-z0-9_]{24,}|gh[pousr]_[A-Za-z0-9_]{30,})\b"),
    "aws_access_key":re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "google_api_key":re.compile(r"\bAIza[0-9A-Za-z_\-]{34}\b"),
    "slack_token":re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{20,}\b"),
    "private_key_block":re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |ENCRYPTED )?PRIVATE KEY-----\s+[A-Za-z0-9+/=\r\n]{100,}-----END (?:RSA |OPENSSH |EC |ENCRYPTED )?PRIVATE KEY-----")
}


def tracked():
    if ROOT.name!="research-crawlers-and-datasets":raise RuntimeError("unexpected root")
    result=subprocess.run(["git","ls-files","-z"],cwd=ROOT,check=True,capture_output=True)
    return sorted({(ROOT/name.decode("utf-8")).resolve() for name in result.stdout.split(b"\0") if name})


def broken_markdown_links(path):
    raw=path.read_text(encoding="utf-8-sig",errors="replace")
    in_fence=False
    problems=[]
    tokens=0
    for line in raw.splitlines():
        if FENCE.match(line):
            in_fence=not in_fence
            continue
        if in_fence:continue
        for match in LINK.finditer(line):
            tokens+=1
            value=match.group(1).strip().strip("<>")
            if not value:continue
            value=value.split(" ",1)[0].split("#",1)[0].split("?",1)[0]
            if not value or value.startswith(("http:","https:","mailto:","tel:","data:","javascript:","C:\\","c:\\","/")):
                continue
            dest=(path.parent/unquote(value)).resolve()
            if not dest.is_relative_to(ROOT) or not dest.exists():
                problems.append(value)
    return tokens,problems


def audit():
    results={"tracked_count":0,"tracked_missing":[],"private_filename_flags":[],
             "secret_pattern_flags":[],"markdown_count":0,"markdown_link_tokens":0,
             "broken_relative_link_count":0,"broken_links_by_section":{},
             "broken_link_sample":[],"large_content_files_not_scanned":0,
             "scope_note":"Current tracked files only; Git/LFS history, third-party rights and external URLs not inspected."}
    broken=[]
    for path in tracked():
        if not path.is_relative_to(ROOT):raise RuntimeError("out-of-root Git path")
        rel=path.relative_to(ROOT).as_posix()
        results["tracked_count"]+=1
        if not path.is_file():
            results["tracked_missing"].append(rel)
            continue
        base=path.name.lower()
        if (base in PRIVATE_NAMES or path.suffix.lower() in PRIVATE_EXT) and "example" not in base and "sample" not in base:
            results["private_filename_flags"].append(rel)
        if path.suffix.lower()==".md":
            results["markdown_count"]+=1
            n,issues=broken_markdown_links(path)
            results["markdown_link_tokens"]+=n
            broken.extend((rel,issue) for issue in issues)
        if path.suffix.lower() not in TEXT_EXT:continue
        if path.stat().st_size>1500000:
            results["large_content_files_not_scanned"]+=1
            continue
        try:content=path.read_text(encoding="utf-8-sig")
        except (UnicodeError,OSError):continue
        for typ,pattern in SECRET_REGEX.items():
            if pattern.search(content):
                results["secret_pattern_flags"].append({"file":rel,"type":typ})
    results["broken_relative_link_count"]=len(broken)
    groups=Counter(f.split("/")[1] if f.startswith("projects/") else f.split("/")[0] for f,_ in broken)
    results["broken_links_by_section"]=dict(groups)
    results["broken_link_sample"]=[{"file":f,"target":t} for f,t in broken[:40]]
    return results


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict-links",action="store_true")
    parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    result=audit()
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    bad=bool(result["tracked_missing"] or result["private_filename_flags"] or result["secret_pattern_flags"])
    return 1 if bad or (args.strict_links and result["broken_relative_link_count"]) else 0


if __name__=="__main__":
    raise SystemExit(main())
