import argparse
import csv
import json
from pathlib import Path


def args():
    base = Path(__file__).resolve().parent
    p = argparse.ArgumentParser(description="Build node/edge JSON from local Facebook analysis CSV files.")
    p.add_argument("--analysis-dir", type=Path, default=base / "analysis-current")
    p.add_argument("--out-dir", type=Path, default=None)
    return p.parse_args()


def read_csv(path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def dot_escape(value):
    text = str(value or "")
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def write_dot(path, nodes, edges):
    lines = [
        "digraph facebook_network {",
        "  rankdir=LR;",
        '  graph [label="Facebook 可见传播网络", labelloc=t, fontsize=18];',
        '  node [fontname="Microsoft YaHei", fontsize=10];',
        '  edge [fontname="Microsoft YaHei", fontsize=9];',
    ]
    for node in nodes:
        shape = "box" if node.get("type") == "post" else "ellipse"
        color = "#2563eb" if node.get("type") == "post" else "#15803d"
        lines.append(
            f'  "{dot_escape(node["id"])}" '
            f'[label="{dot_escape(node.get("label") or node.get("id"))}", shape={shape}, color="{color}"];'
        )
    for edge in edges:
        label = edge.get("type") or "relation"
        if edge.get("unresolved_source_post"):
            label += " (unresolved source)"
        lines.append(
            f'  "{dot_escape(edge["source"])}" -> "{dot_escape(edge["target"])}" '
            f'[label="{dot_escape(label)}"];'
        )
    lines.append("}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    options = args()
    out_dir = options.out_dir or options.analysis_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    posts = read_csv(options.analysis_dir / "facebook-posts.csv")
    shares = read_csv(options.analysis_dir / "facebook-share-users.csv")
    reactors = read_csv(options.analysis_dir / "facebook-reactor-users.csv")

    nodes = {}
    edges = []
    post_keys = {post.get("post_id"): f"post:{post.get('post_id')}" for post in posts if post.get("post_id")}
    for post in posts:
        post_id = post.get("post_id")
        if not post_id:
            continue
        post_node = f"post:{post_id}"
        nodes[post_node] = {
            "id": post_node,
            "type": "post",
            "label": post.get("author_name") or post_id,
            "post_id": post_id,
            "url": post.get("url"),
        }
        for share in shares:
            if share.get("source_post_id") != post_id:
                continue
            actor = share.get("actor_id")
            if not actor:
                continue
            actor_node = f"user:{actor}"
            nodes.setdefault(actor_node, {"id": actor_node, "type": "user", "label": share.get("actor_name") or actor})
            edges.append(
                {
                    "source": actor_node,
                    "target": post_node,
                    "type": "share",
                    "user_name": share.get("actor_name"),
                    "share_story_id": share.get("share_story_id"),
                }
            )
        for reactor in reactors:
            if reactor.get("source_post_id") != post_id:
                continue
            actor = reactor.get("user_id")
            if not actor:
                continue
            actor_node = f"user:{actor}"
            nodes.setdefault(actor_node, {"id": actor_node, "type": "user", "label": reactor.get("user_name") or actor})
            edges.append(
                {
                    "source": actor_node,
                    "target": post_node,
                    "type": "reaction",
                    "user_name": reactor.get("user_name"),
                    "reaction_id": reactor.get("reaction_id"),
                    "source_field": reactor.get("source_field"),
                }
            )

    unresolved_reactions = [
        reactor for reactor in reactors
        if reactor.get("source_post_id") not in post_keys
    ]
    for reactor in unresolved_reactions:
        source_id = reactor.get("source_post_id")
        if not source_id:
            continue
        post_node = f"post:{source_id}"
        nodes.setdefault(post_node, {"id": post_node, "type": "post", "label": "unresolved source post", "post_id": source_id})
        actor = reactor.get("user_id")
        if not actor:
            continue
        actor_node = f"user:{actor}"
        nodes.setdefault(actor_node, {"id": actor_node, "type": "user", "label": reactor.get("user_name") or actor})
        edges.append({
            "source": actor_node,
            "target": post_node,
            "type": "reaction",
            "user_name": reactor.get("user_name"),
            "reaction_id": reactor.get("reaction_id"),
            "source_field": reactor.get("source_field"),
            "unresolved_source_post": True,
        })

    unresolved_shares = [
        share for share in shares
        if share.get("source_post_id") not in post_keys
    ]
    for share in unresolved_shares:
        source_id = share.get("source_post_id")
        if not source_id:
            continue
        post_node = f"post:{source_id}"
        nodes.setdefault(post_node, {"id": post_node, "type": "post", "label": "unresolved source post", "post_id": source_id})
        actor = share.get("actor_id")
        if not actor:
            continue
        actor_node = f"user:{actor}"
        nodes.setdefault(actor_node, {"id": actor_node, "type": "user", "label": share.get("actor_name") or actor})
        edges.append({
            "source": actor_node,
            "target": post_node,
            "type": "share",
            "user_name": share.get("actor_name"),
            "share_story_id": share.get("share_story_id"),
            "unresolved_source_post": True,
        })

    result = {
        "scope": "local Facebook analysis CSVs",
        "nodes": list(nodes.values()),
        "edges": edges,
        "notes": [
            "Edges only represent users visible in the local capture.",
            "Share edges are linked by the source feedback/post ID; unresolved source posts are explicitly marked.",
            "Reaction counts without reactor user nodes do not create reaction edges.",
            "Reaction edges retain the GraphQL source_field when available.",
            "This script does not access Facebook or replay requests.",
        ],
    }
    (out_dir / "facebook-network.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_dot(out_dir / "facebook-network.dot", result["nodes"], result["edges"])
    with (out_dir / "facebook-network-edges.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["source", "target", "type", "user_name", "share_story_id", "reaction_id", "source_field", "unresolved_source_post"],
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(edges)
    print(json.dumps({
        "nodes": len(result["nodes"]),
        "edges": len(edges),
        "unresolved_share_edges": sum(1 for edge in edges if edge.get("unresolved_source_post")),
        "dot": str(out_dir / "facebook-network.dot"),
        "out_dir": str(out_dir),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
