import argparse
import json
from collections import defaultdict
from pathlib import Path

from analyze_facebook_capture import decode_stream, first_actor, walk, _reactor_nodes


def friendly_name(record):
    return (
        record.get("friendly_name")
        or (record.get("request_headers") or {}).get("x-fb-friendly-name")
        or "(unnamed)"
    )


def inspect_record(record):
    result = {
        "share_connections": 0,
        "share_identities": 0,
        "share_page_info": 0,
        "reaction_connections": 0,
        "reaction_identities": 0,
        "reaction_page_info": 0,
    }
    for document in decode_stream(record.get("response_body") or ""):
        for value in walk(document):
            if not isinstance(value, dict):
                continue

            connection = value.get("reshares")
            if isinstance(connection, dict) and isinstance(connection.get("edges"), list):
                result["share_connections"] += 1
                if isinstance(connection.get("page_info"), dict):
                    result["share_page_info"] += 1
                for edge in connection.get("edges") or []:
                    node = (edge or {}).get("node") or {}
                    actor = first_actor(node)
                    if actor.get("id") or actor.get("strong_id__") or actor.get("name"):
                        result["share_identities"] += 1

            for field in ("reactors", "important_reactors", "friend_reactors"):
                if field not in value:
                    continue
                connection = value.get(field)
                if not isinstance(connection, (dict, list)):
                    continue
                result["reaction_connections"] += 1
                if isinstance(connection, dict) and isinstance(connection.get("page_info"), dict):
                    result["reaction_page_info"] += 1
                for actor in _reactor_nodes(connection):
                    if actor.get("id") or actor.get("strong_id__") or actor.get("name"):
                        result["reaction_identities"] += 1
    return result


def scan(root):
    totals = defaultdict(
        lambda: {
            "share_connections": 0,
            "share_identities": 0,
            "share_page_info": 0,
            "reaction_connections": 0,
            "reaction_identities": 0,
            "reaction_page_info": 0,
            "records": 0,
            "runs": set(),
        }
    )
    files = sorted(Path(root).rglob("safe-facebook-http-capture.jsonl"))
    parse_errors = 0
    for path in files:
        run_name = path.parent.name
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    parse_errors += 1
                    continue
                evidence = inspect_record(record)
                if not evidence["share_connections"] and not evidence["reaction_connections"]:
                    continue
                row = totals[friendly_name(record)]
                row["records"] += 1
                row["runs"].add(run_name)
                for key, value in evidence.items():
                    row[key] += value
    return files, parse_errors, totals


def main():
    parser = argparse.ArgumentParser(description="Mine local Facebook captures for identity-bearing Share/Reaction response shapes.")
    parser.add_argument("--root", type=Path, default=Path("runs"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    files, parse_errors, totals = scan(args.root)
    rows = []
    for name, row in totals.items():
        item = {"friendly_name": name, **{k: v for k, v in row.items() if k != "runs"}, "run_count": len(row["runs"])}
        rows.append(item)
    rows.sort(key=lambda r: (-(r["share_identities"] + r["reaction_identities"]), r["friendly_name"]))

    if args.json:
        print(json.dumps({"capture_files": len(files), "parse_errors": parse_errors, "families": rows}, ensure_ascii=False, indent=2))
        return

    print(f"capture_files={len(files)} parse_errors={parse_errors} identity_bearing_families={len(rows)}")
    for row in rows:
        print(
            "{friendly_name}\trecords={records} runs={run_count} "
            "share_conn={share_connections} share_users={share_identities} share_page_info={share_page_info} "
            "reaction_conn={reaction_connections} reaction_users={reaction_identities} reaction_page_info={reaction_page_info}".format(**row)
        )


if __name__ == "__main__":
    main()
