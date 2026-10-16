"""Fail-closed, offline proof for the observed group album -> comments route."""
from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit


def require(value, reason):
    if not value:
        raise ValueError(reason)
    return value


def group_target(url):
    u = urlsplit(str(url))
    require(u.scheme == "https" and u.hostname in {"www.facebook.com", "m.facebook.com", "facebook.com"}, "not-facebook-url")
    m = re.fullmatch(r"/groups/(\d{1,32})/(?:posts|permalink)/(\d{1,32})/?", u.path)
    require(m, "not-exact-group-post-url")
    return m.groups()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def verify_report(report, url, post_id, min_line):
    group, post = group_target(url)
    require(not post_id or str(post_id) == post, "requested-post-mismatch")
    tv = report.get("target_verification", {})
    require(int(report.get("min_line", 0)) == int(min_line) and int(min_line) > 0, "window-mismatch")
    require(tv.get("status") == "matched" and group_target(tv.get("expected_url", "")) == (group, post), "verification-target-mismatch")
    require(int(tv.get("matched_line", 0)) >= int(min_line), "stale-match")
    require(str(tv.get("matched_post_id") or tv.get("matched_token") or "") == post, "matched-post-missing-or-wrong")
    rows = [p for p in report.get("posts", []) if str(p.get("post_id", "")) == post]
    require(len(rows) == 1, "target-story-not-unique")
    row = rows[0]
    require(group_target(row.get("url", "")) == (group, post), "story-group-mismatch")
    require(re.fullmatch(r"\d{1,32}", str(row.get("author_id", ""))) and row.get("author_name") and row.get("message"), "story-identity-incomplete")
    return row


def label(n):
    return n.get("text") or n.get("content-desc", "")


def bounds(n):
    m = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", n.get("bounds", ""))
    require(m, "invalid-bounds")
    b = tuple(map(int, m.groups()))
    require(0 <= b[0] < b[2] <= 1080 and 0 <= b[1] < b[3] <= 1920, "invalid-bounds")
    return b


def is_options(n):
    return bool(re.search(r"(?:帖子的更多选项|貼文的更多選項|post.*more\s+options)", label(n), re.I))


def surface(path):
    tree = ET.parse(path)
    nodes = list(tree.iter("node"))
    require(all(n.get("package") == "com.facebook.katana" for n in nodes if n.get("package")), "foreign-package")
    require(not any(n.get("class", "").endswith(("EditText", "AutoCompleteTextView")) for n in nodes), "detail-or-composer")
    back = [n for n in nodes if label(n) in {"返回", "Back"} and n.get("clickable") == "true" and bounds(n)[2] <= 200 and bounds(n)[3] <= 220]
    search = [n for n in nodes if n.get("clickable") == "true" and bounds(n)[1] <= 220 and re.search(r"(?:^在.+中搜索$|search\s+(?:in|this)\b)", label(n), re.I)]
    lists = [n for n in nodes if n.get("class", "").endswith("RecyclerView") and n.get("scrollable") == "true" and bounds(n)[0] == 0 and bounds(n)[2] == 1080]
    require(len(back) == len(search) == len(lists) == 1, "not-unique-group-surface")
    return lists[0], label(search[0])


def album(row):
    photos = {}
    total = None
    for n in row.iter("node"):
        m = re.fullmatch(r"第(\d+)张照片，共(\d+)张照片，点击可展开照片", label(n))
        if not m:
            continue
        require(n.get("class") == "android.widget.Button" and n.get("clickable") == "false", "unrecognized-album-node")
        index, count = map(int, m.groups())
        require(index not in photos and 1 <= index <= count and (total is None or total == count), "ambiguous-album")
        total = count
        photos[index] = bounds(n)
    require(total and total >= 5 and len(photos) >= 2, "no-observed-multi-photo-album")
    overflow = [n for n in row.iter("node") if label(n) == f"+{total - 4}"]
    require(len(overflow) == 1, "album-overflow-missing")
    photos[0] = bounds(overflow[0])
    return total, photos


def initial(path, story):
    scroller, header = surface(path)
    options = [n for n in scroller.iter("node") if is_options(n)]
    require(len(options) == 1 and options[0].get("clickable") == "true", "initial-story-options-not-unique")
    author = str(story["author_name"])
    expected = {author + "帖子的更多选项", author + "貼文的更多選項", author + "'s post more options"}
    require(label(options[0]) in expected, "author-mismatch")
    rows = [r for r in scroller if options[0] in list(r.iter("node"))]
    require(len(rows) == 1, "options-not-in-direct-story-row")
    row = rows[0]
    require(sum(label(n).strip() == str(story["message"]).strip() for n in row.iter("node")) == 1, "caption-mismatch-or-duplicate")
    require(not any(n.get("class", "").endswith("ImageView") and label(n) in {"Photo", "照片", "相片"} for n in row.iter("node")), "mixed-or-nested-photo")
    count, photos = album(row)
    return {"header": header, "viewport": bounds(scroller), "count": count, "photos": photos}


def footer(path, anchor):
    scroller, header = surface(path)
    require(header == anchor["header"] and bounds(scroller) == anchor["viewport"], "group-surface-changed")
    row = require(list(scroller), "empty-list")[0]
    require(not any(is_options(n) for n in row.iter("node")), "target-header-not-clipped")
    count, photos = album(row)
    require(count == anchor["count"] and set(photos) <= set(anchor["photos"]), "album-identity-changed")
    top, bottom = anchor["viewport"][1], anchor["viewport"][3]
    deltas = set()
    for i, b in photos.items():
        old = anchor["photos"][i]
        require(old[::2] == b[::2], "album-columns-changed")
        if b[1] > top and old[1] > top:
            deltas.add(old[1] - b[1])
        if b[3] < bottom and old[3] < bottom:
            deltas.add(old[3] - b[3])
    require(len(deltas) == 1 and 50 <= next(iter(deltas)) <= 1200, "album-scroll-continuity-unproved")
    delta = next(iter(deltas))
    for i, b in photos.items():
        old = anchor["photos"][i]
        if old[1] > top:
            require(b[1] == max(top, old[1] - delta), "album-top-discontinuity")
        if old[3] < bottom:
            require(b[3] == max(top, old[3] - delta), "album-bottom-discontinuity")
    comments = [n for n in row.iter("node") if label(n) in {"评论", "評論", "Comment"} and n.get("class") == "android.widget.Button" and n.get("clickable") == "true"]
    require(len(comments) == 1, "target-footer-comment-not-unique")
    node = comments[0]
    parent = next(p for p in row.iter("node") if node in list(p))
    siblings = list(parent)
    require(any(re.match(r"^(?:赞按钮|讚按鈕|Like)", label(n), re.I) for n in siblings) and any(re.match(r"^(?:分享按钮|分享按鈕|Share)", label(n), re.I) for n in siblings), "not-story-action-row")
    b = bounds(node)
    require(100 <= b[0] < b[2] <= 500 and 60 <= b[3] - b[1] <= 180 and 0 <= b[1] - max(p[3] for p in photos.values()) <= 8, "footer-not-adjacent-to-target-album")
    return {"bounds": node.get("bounds"), "x1": b[0], "y1": b[1], "x2": b[2], "y2": b[3]}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=["bind", "initial", "footer", "verify-context"])
    for name in ["xml", "report", "url", "post-id", "min-line", "diagnostic", "before"]:
        p.add_argument("--" + name, default="")
    a = p.parse_args()
    if a.mode == "bind":
        story = verify_report(load(a.report), a.url, a.post_id, int(a.min_line))
        initial(a.xml, story)
        result = {"post_id": story["post_id"], "author_id": story["author_id"]}
    else:
        diag = load(a.diagnostic)
        require(diag.get("verified") is True and diag.get("inline_group_share_route") is True, "route-not-opted-in")
        require(str(diag.get("resolved_post_id", "")) == a.post_id and a.post_id, "diagnostic-post-mismatch")
        report_path = Path(a.diagnostic).parent / "verification-analysis/facebook-sample-patterns.json"
        story = verify_report(load(report_path), diag["resolved_url"], a.post_id, diag["verification_min_line"])
        original = initial(diag["ui_path"], story)
        if a.mode == "verify-context":
            result = {"matched": True}
        else:
            anchor = initial(a.xml if a.mode == "initial" else a.before, story)
            require(anchor == original, "fresh-initial-card-changed")
            result = {"matched": True} if a.mode == "initial" else footer(a.xml, anchor)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, ET.ParseError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=True))
        raise SystemExit(3)
