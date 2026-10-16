from __future__ import annotations

import argparse
import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from urllib.request import ProxyHandler, Request, build_opener


FACEBOOK_HOSTS = {"facebook.com", "www.facebook.com", "m.facebook.com"}


def canonical_facebook_target(value: str, depth: int = 0) -> str | None:
    if depth > 2:
        return None
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        return None
    if (parsed.hostname or "").lower() not in FACEBOOK_HOSTS:
        return None
    path = parsed.path or "/"
    query = parse_qs(parsed.query, keep_blank_values=True)
    if path.rstrip("/").lower() == "/login":
        target = str((query.get("next") or [""])[0]).strip()
        return canonical_facebook_target(target, depth + 1) if target else None

    clean_path = None
    for pattern in (
        r"/reel/\d+/?",
        r"/videos/\d+/?",
        r"/groups/\d+/permalink/\d+/?",
        r"/(?:\d+|[^/]+)/posts/(?:\d+|pfbid[A-Za-z0-9]+)/?",
    ):
        if re.fullmatch(pattern, path, flags=re.IGNORECASE):
            clean_path = path.rstrip("/") + "/"
            break
    if clean_path:
        return urlunsplit(("https", "www.facebook.com", clean_path, "", ""))

    if path.rstrip("/").lower() == "/story.php":
        story_id = str((query.get("story_fbid") or [""])[0]).strip()
        owner_id = str((query.get("id") or [""])[0]).strip()
        if not re.fullmatch(r"(?:\d+|pfbid[A-Za-z0-9]+)", story_id) or not owner_id.isdigit():
            return None
        clean_query = {"story_fbid": story_id, "id": owner_id}
        post_id = str((query.get("post_id") or [""])[0]).strip()
        if re.fullmatch(r"\d+_(?:\d+|pfbid[A-Za-z0-9]+)", post_id):
            clean_query["post_id"] = post_id
        return urlunsplit(("https", "www.facebook.com", "/story.php", urlencode(clean_query), ""))
    return None


def resolve_share_url(url: str, proxy: str, timeout_seconds: float = 15.0) -> str | None:
    handlers = [ProxyHandler({"http": proxy, "https": proxy})] if proxy else [ProxyHandler({})]
    opener = build_opener(*handlers)
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"}, method="HEAD")
    try:
        with opener.open(request, timeout=max(1.0, timeout_seconds)) as response:
            final_url = response.geturl()
    except HTTPError as exc:
        final_url = exc.geturl()
    except (OSError, URLError, ValueError):
        return None
    return canonical_facebook_target(final_url)


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve one Facebook share URL to a canonical post or reel URL.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--proxy", default="http://127.0.0.1:7897")
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    args = parser.parse_args()
    resolved = resolve_share_url(args.url, args.proxy, args.timeout_seconds)
    if not resolved:
        return 2
    print(resolved)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
