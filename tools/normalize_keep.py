
#!/usr/bin/env python3
# These commands only use already anonymized first-phase JSON and do not contact Keep.
import argparse
import json
import csv
from pathlib import Path

def collect(src: Path, output: Path):
    for stage in ("profiles", "activities"):
        source=src / stage
        if not source.exists():
            raise SystemExit(f"Missing first-phase {stage}: {source}")
    profiles={};posts={};comments=[]
    for file in (src/"profiles").glob("*.json"):
        o=json.loads(file.read_text(encoding="utf-8"))
        info=(((o.get("data") or {}).get("headInfo") or {}).get("userBasicInfo") or {})
        if not isinstance(info,dict):continue
        uid=info.get("id")
        if uid:profiles[uid]={"profile_id":uid,"gender":info.get("gender"),"keep_value":info.get("keepValue"),"kg_level":info.get("kgLevel"),"member_status":info.get("memberStatus")}
    for file in (src/"activities").glob("*.json"):
        o=json.loads(file.read_text(encoding="utf-8"))
        for group in o.get("data",[]):
            info=group.get("info") if isinstance(group,dict) else {}
            for entry in ((info.get("entries") if isinstance(info,dict) else []) or []):
                if not isinstance(entry,dict):continue
                pid=entry.get("id")
                if not pid:continue
                author=(entry.get("author") or {})
                posts[pid]={"post_id":pid,"author_id":author.get("_id") if isinstance(author,dict) else None,
                            "type":entry.get("type"),"state":entry.get("state"),
                            "internal_share_count":entry.get("internalShareCount")}
                for i,comment in enumerate(entry.get("sampleComments") or []):
                    if isinstance(comment,dict):
                        comments.append({"comment_id":comment.get("id") or pid+":"+str(i),
                        "post_id":pid,"sample_comment_index":i})
    def save(name,fields,records):
        target=output/name
        target.parent.mkdir(parents=True,exist_ok=True)
        with target.open("w",encoding="utf-8",newline="") as f:
            w=csv.DictWriter(f,fieldnames=fields)
            w.writeheader();w.writerows(records)
    save("profiles.csv",["profile_id","gender","keep_value","kg_level","member_status"],list(profiles.values()))
    save("posts.csv",["post_id","author_id","type","state","internal_share_count"],list(posts.values()))
    save("comments.csv",["comment_id","post_id","sample_comment_index"],comments)
    print(f"profiles={len(profiles)} posts={len(posts)} comments={len(comments)}")

if __name__=="__main__":
    ap=argparse.ArgumentParser(description="Normalize already-anonymized Keep capture responses")
    ap.add_argument("--input",type=Path,default=Path(__file__).resolve().parents[1]/"projects/social/keep/data/phase1_anonymized")
    ap.add_argument("--output",type=Path,default=Path(__file__).resolve().parents[1]/"projects/social/keep/data/phase2")
    args=ap.parse_args()
    collect(args.input,args.output)
