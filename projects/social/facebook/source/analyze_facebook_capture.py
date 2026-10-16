import argparse
import base64
import binascii
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit


REACTION_QUERY_PREFIXES = ("FeedbackReactorsGraphService", "FbShortsIGMediaReactorsQuery")


def is_reaction_query(name):
    return str(name or "").startswith(REACTION_QUERY_PREFIXES)


def parse_args():
    base = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Analyze a local, already captured Facebook JSONL file.")
    parser.add_argument("--input", type=Path, default=base / "safe-facebook-http-capture.jsonl")
    parser.add_argument("--out-dir", type=Path, default=base / "analysis")
    parser.add_argument(
        "--max-records",
        type=int,
        default=0,
        help="Maximum JSONL records to analyze; 0 reads the complete capture (default)",
    )
    parser.add_argument("--redact-ids", action="store_true", help="Hash public IDs before writing CSV/JSON outputs")
    parser.add_argument("--expected-post-id")
    parser.add_argument("--expected-url")
    parser.add_argument("--share-target-input-url", help="Original input URL; opt in to Share-only provenance columns")
    parser.add_argument("--share-min-line", type=int, default=1, help="Fresh Share phase boundary; navigation/Reaction records remain available for target verification")
    parser.add_argument("--require-target-match", action="store_true")
    parser.add_argument("--min-line", type=int, default=1, help="Ignore JSONL records before this 1-based line number")
    parser.add_argument("--focus-source-post-id", help="Limit share/reaction identity results to one exact source post")
    return parser.parse_args()


def target_url_tokens(url):
    value = unquote(str(url or ""))
    tokens = []
    for pattern in (
        r"(pfbid[A-Za-z0-9]+)",
        r"[?&]story_fbid=([^&#]+)",
        r"/posts/([0-9]+)",
        r"/reel/([0-9]+)",
        r"/videos/([0-9]+)",
        r"/permalink/([0-9]+)",
    ):
        for match in re.finditer(pattern, value, flags=re.IGNORECASE):
            token = match.group(1)
            if token and token not in tokens:
                tokens.append(token)
    return tokens


def _target_url_components(url):
    """Return exact identity components carried by supported Facebook URLs."""
    value = unquote(str(url or "").strip())
    empty = {
        "pfbid": set(),
        "story_fbid": set(),
        "post_id": set(),
        "reel_id": set(),
        "video_id": set(),
        "permalink_id": set(),
    }
    if not value:
        return empty
    try:
        parsed = urlsplit(value)
    except ValueError:
        return empty
    if parsed.scheme.lower() not in {"http", "https"}:
        return empty
    if (parsed.hostname or "").lower() not in {
        "facebook.com",
        "www.facebook.com",
        "m.facebook.com",
    }:
        return empty
    try:
        if parsed.port not in (None, 80, 443):
            return empty
    except ValueError:
        return empty
    query = parse_qs(parsed.query, keep_blank_values=True)
    components = {
        "pfbid": set(),
        "story_fbid": set(),
        "post_id": set(),
        "reel_id": set(),
        "video_id": set(),
        "permalink_id": set(),
    }
    for match in re.finditer(r"/posts/(pfbid[A-Za-z0-9]+|\d+)(?:/|$)", parsed.path, flags=re.IGNORECASE):
        token = match.group(1)
        components["pfbid" if token.lower().startswith("pfbid") else "post_id"].add(token)
    for key, component in (("story_fbid", "story_fbid"), ("post_id", "post_id")):
        for token in query.get(key, []):
            if re.fullmatch(r"\d+", token):
                components[component].add(token)
    for pattern, component in (
        (r"/reel/(\d+)(?:/|$)", "reel_id"),
        (r"/videos/(\d+)(?:/|$)", "video_id"),
        (r"/(?:groups/\d+/)?permalink/(\d+)(?:/|$)", "permalink_id"),
    ):
        components[component].update(re.findall(pattern, parsed.path, flags=re.IGNORECASE))
    return components


def _url_component_match(expected_url, observed_url):
    """Compare supported URL identities as complete components."""
    expected = _target_url_components(expected_url)
    observed = _target_url_components(observed_url)
    if expected["pfbid"] & observed["pfbid"]:
        return True
    if expected["reel_id"] & observed["reel_id"]:
        return True
    if expected["video_id"] & observed["video_id"]:
        return True
    expected_numeric = expected["post_id"] | expected["story_fbid"] | expected["permalink_id"]
    observed_numeric = observed["post_id"] | observed["story_fbid"] | observed["permalink_id"]
    return bool(expected_numeric & observed_numeric)


def _matched_url_token(expected_url, observed_url):
    expected = _target_url_components(expected_url)
    observed = _target_url_components(observed_url)
    for token in target_url_tokens(expected_url):
        if token.lower().startswith("pfbid") and token in observed["pfbid"]:
            return token
        if token in observed["reel_id"] and token in expected["reel_id"]:
            return token
        if token in observed["video_id"] and token in expected["video_id"]:
            return token
        observed_numeric = observed["post_id"] | observed["story_fbid"] | observed["permalink_id"]
        expected_numeric = expected["post_id"] | expected["story_fbid"] | expected["permalink_id"]
        if token in expected_numeric and token in observed_numeric:
            return token
    return None


def _post_identity_candidates(documents):
    """Collect exact Story identities from decoded GraphQL documents."""
    candidates = []
    for document in documents:
        for value in walk(document):
            if not isinstance(value, dict) or value.get("__typename") not in ("Story", "PartialStory"):
                continue
            feedback = value.get("feedback")
            feedback = feedback if isinstance(feedback, dict) else {}
            post_id = str(
                value.get("post_id")
                or value.get("legacy_api_post_id")
                or feedback.get("legacy_api_post_id")
                or ""
            ).strip()
            actor = first_actor(value)
            owner = feedback.get("owning_profile")
            owner = owner if isinstance(owner, dict) else {}
            author_id = str(actor.get("id") or owner.get("id") or "").strip()
            if not post_id:
                continue
            candidates.append(
                {
                    "post_id": post_id,
                    "author_id": author_id,
                    "url": value.get("url") or value.get("wwwURL") or feedback.get("url"),
                }
            )
    return candidates


def _decoded_url_values(documents):
    """Yield URLs from decoded Story fields, excluding arbitrary metadata."""
    for document in documents:
        for value in walk(document):
            if not isinstance(value, dict) or value.get("__typename") not in ("Story", "PartialStory"):
                continue
            for key, candidate in value.items():
                if not isinstance(candidate, str):
                    continue
                normalized_key = str(key).lower()
                if normalized_key == "url" or normalized_key.endswith("url"):
                    yield candidate


def _decoded_video_ids(documents):
    """Yield IDs from decoded video-shaped fields only."""
    for document in documents:
        for value in walk(document):
            if not isinstance(value, dict):
                continue
            video = value.get("video")
            if isinstance(video, dict):
                candidate = video.get("id")
                if isinstance(candidate, (str, int)) and re.fullmatch(r"\d{1,32}", str(candidate)):
                    yield str(candidate)
            if value.get("__typename") in {"Video", "VideoMedia", "Reel"}:
                candidate = value.get("id")
                if isinstance(candidate, (str, int)) and re.fullmatch(r"\d{1,32}", str(candidate)):
                    yield str(candidate)
                for key in ("video_id", "videoId", "videoID"):
                    candidate = value.get(key)
                    if isinstance(candidate, (str, int)) and re.fullmatch(r"\d{1,32}", str(candidate)):
                        yield str(candidate)


def _reel_video_story_mappings(documents, expected_reel_id):
    """Return only explicit media-to-Story mappings for a canonical Reel.

    A Reel media token and a later feedback source are independent observations;
    they must not be joined by ordering or uniqueness.  This helper requires the
    same Story object to carry the media token and a complete Story identity.
    """
    reel_id = str(expected_reel_id or "").strip()
    if not re.fullmatch(r"\d{1,32}", reel_id):
        return []
    mappings = {}

    def media_ids(value):
        if not isinstance(value, dict):
            return set()
        found = set()
        for key in ("id", "strong_id__", "video_id", "videoId", "videoID"):
            candidate = value.get(key)
            if re.fullmatch(r"\d{1,32}", str(candidate or "")):
                found.add(str(candidate))
        return found

    for document in documents:
        for story in walk(document):
            if not isinstance(story, dict) or story.get("__typename") not in ("Story", "PartialStory"):
                continue
            media_values = []
            for key in ("video_media_attachment", "video_media_attachment_info", "video_media", "media"):
                candidate = story.get(key)
                if isinstance(candidate, list):
                    media_values.extend(candidate)
                elif isinstance(candidate, dict):
                    media_values.append(candidate)
            token_seen = False
            for item in media_values:
                if not isinstance(item, dict):
                    continue
                token_seen = token_seen or reel_id in media_ids(item)
                media = item.get("media")
                token_seen = token_seen or reel_id in media_ids(media)
            if not token_seen:
                continue
            feedback = story.get("feedback") if isinstance(story.get("feedback"), dict) else {}
            actor = first_actor(story)
            owner = feedback.get("owning_profile") if isinstance(feedback.get("owning_profile"), dict) else {}
            post_id = str(story.get("post_id") or story.get("legacy_api_post_id") or feedback.get("legacy_api_post_id") or "").strip()
            author_id = str(actor.get("id") or owner.get("id") or "").strip()
            relay = relay_story_identity(story.get("id"))
            if relay:
                if post_id and post_id != relay["post_id"]:
                    # Reel Relay IDs can identify media while Story.post_id and
                    # feedback identify the reactor source. Accept that namespace
                    # split only with explicit same-object, fully coherent proof.
                    story_post = str(story.get("post_id") or "").strip()
                    feedback_post = str(feedback.get("legacy_api_post_id") or "").strip()
                    legacy_post = str(story.get("legacy_api_post_id") or "").strip()
                    actor_id = str(actor.get("id") or "").strip()
                    owner_id = str(owner.get("id") or "").strip()
                    if not (
                        relay["post_id"] == reel_id
                        and re.fullmatch(r"\d{1,32}", story_post)
                        and story_post == feedback_post == post_id
                        and legacy_post in ("", story_post)
                        and actor_id == owner_id == relay["author_id"]
                    ):
                        continue
                    author_id = relay["author_id"]
                else:
                    if author_id and author_id != relay["author_id"]:
                        continue
                    post_id, author_id = relay["post_id"], relay["author_id"]
            if not (re.fullmatch(r"\d{1,32}", post_id) and re.fullmatch(r"\d{1,32}", author_id)):
                continue
            mappings[(post_id, author_id)] = {"post_id": post_id, "author_id": author_id}
    return list(mappings.values())


def _story_candidates_are_unambiguous(candidates, expected_author_id=None):
    """Reject conflicting raw Story authors before any aggregate dedupe."""
    author_ids = {
        str(item.get("author_id") or "").strip()
        for item in candidates
        if str(item.get("author_id") or "").strip()
    }
    if len(author_ids) > 1:
        return False
    expected_author_id = str(expected_author_id or "").strip()
    return not expected_author_id or not author_ids or author_ids == {expected_author_id}


def _story_candidates_for_post(candidates, expected_post_id, expected_url):
    expected_post_id = str(expected_post_id or "").strip()
    if expected_post_id:
        return [item for item in candidates if item["post_id"] == expected_post_id]
    components = _target_url_components(expected_url)
    expected_numeric = components["post_id"] | components["story_fbid"] | components["permalink_id"]
    if not expected_numeric:
        return []
    return [item for item in candidates if item["post_id"] in expected_numeric]


def _external_post_link_identity(candidates, expected_post_id, expected_author_id, expected_url):
    """Resolve one ExternalPostLink Story without hiding author conflicts."""
    by_post = {}
    for item in candidates:
        post_id = item["post_id"]
        by_post.setdefault(post_id, set()).add(item.get("author_id") or "")
    if expected_post_id:
        post_ids = {expected_post_id}
    else:
        components = _target_url_components(expected_url)
        expected_numeric = components["post_id"] | components["story_fbid"] | components["permalink_id"]
        post_ids = expected_numeric
    matching = [(post_id, authors) for post_id, authors in by_post.items() if post_id in post_ids]
    if len(matching) != 1:
        return None
    post_id, author_ids = matching[0]
    concrete_author_ids = {author_id for author_id in author_ids if author_id}
    if concrete_author_ids != {str(expected_author_id)}:
        return None
    return {"post_id": post_id, "author_id": str(expected_author_id)}


def target_url_digest(url):
    value = str(url or "").strip()
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else None


def facebook_navigation_url_digests(url):
    """Hash the exact URL plus Facebook's observed www-to-root host rewrite."""
    value = str(url or "").strip()
    if not value:
        return set()
    digests = {target_url_digest(value)}
    try:
        parsed = urlsplit(value)
    except ValueError:
        return digests
    host = (parsed.hostname or "").lower()
    if host == "www.facebook.com":
        digests.add(target_url_digest(parsed._replace(netloc="facebook.com").geturl()))
    elif host == "facebook.com":
        digests.add(target_url_digest(parsed._replace(netloc="www.facebook.com").geturl()))
    return digests


def relay_story_identity(value):
    """Decode only the two observed Facebook Story Relay ID layouts."""
    encoded = str(value or "").strip()
    if not encoded or len(encoded) > 4096:
        return None
    try:
        decoded = base64.b64decode(encoded + "=" * (-len(encoded) % 4), validate=True).decode("ascii")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    match = re.fullmatch(r"S:_I(\d+):(\d+):\2", decoded)
    if not match:
        match = re.fullmatch(r"S:_I(\d+):VK:(\d+)", decoded)
    if not match:
        return None
    return {"author_id": match.group(1), "post_id": match.group(2)}


def _url_resolver_story_id(body):
    ids = set()
    for document in decode_stream(body):
        for value in walk(document):
            resolver = value.get("urlResolver") if isinstance(value, dict) else None
            if isinstance(resolver, dict) and resolver.get("__typename") in {"Story", "PartialStory"}:
                relay_id = str(resolver.get("id") or "").strip()
                if relay_id and relay_story_identity(relay_id):
                    ids.add(relay_id)
    return next(iter(ids)) if len(ids) == 1 else None


def _static_story_id(body):
    ids = set()
    for document in decode_stream(body):
        data = document.get("data") if isinstance(document, dict) else None
        node = data.get("node") if isinstance(data, dict) else None
        if isinstance(node, dict) and node.get("__typename") in {"Story", "PartialStory"}:
            relay_id = str(node.get("id") or "").strip()
            if relay_id and relay_story_identity(relay_id):
                ids.add(relay_id)
    return next(iter(ids)) if len(ids) == 1 else None


def _reel_seed_story_identity(body, seed_video_id):
    seed = str(seed_video_id or "").strip()
    if not re.fullmatch(r"\d{1,32}", seed):
        return None
    matches = {}
    for document in decode_stream(body):
        data = document.get("data") if isinstance(document, dict) else None
        viewer = data.get("viewer") if isinstance(data, dict) else None
        feed = viewer.get("video_feed_unit_feed") if isinstance(viewer, dict) else None
        edges = feed.get("edges") if isinstance(feed, dict) else None
        if not isinstance(edges, list):
            continue
        for edge in edges:
            node = edge.get("node") if isinstance(edge, dict) else None
            infos = node.get("video_media_attachment_info") if isinstance(node, dict) else None
            if not isinstance(infos, list):
                continue
            for info in infos:
                media = info.get("media") if isinstance(info, dict) else None
                if not isinstance(media, dict) or str(media.get("id") or "") != seed:
                    continue
                story = media.get("container_story")
                relay_id = str(story.get("id") or "").strip() if isinstance(story, dict) else ""
                identity = relay_story_identity(relay_id)
                if identity:
                    matches[(identity["post_id"], identity["author_id"])] = identity
    return next(iter(matches.values())) if len(matches) == 1 else None


def target_url_author_id(url):
    value = unquote(str(url or ""))
    if not value:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    path_match = re.search(r"/(\d+)/posts/(?:pfbid[A-Za-z0-9]+|\d+)", parsed.path, flags=re.IGNORECASE)
    if path_match:
        return path_match.group(1)
    query = parse_qs(parsed.query, keep_blank_values=True)
    for key in ("id", "owner_id"):
        candidate = str((query.get(key) or [""])[0]).strip()
        if candidate.isdigit():
            return candidate
    post_id = str((query.get("post_id") or [""])[0]).strip()
    match = re.match(r"^(\d+)_pfbid[A-Za-z0-9]+$", post_id, flags=re.IGNORECASE)
    return match.group(1) if match else None


def is_facebook_pfbid_post_url(url):
    value = unquote(str(url or "").strip())
    if not value:
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if (parsed.hostname or "").lower() not in {"facebook.com", "www.facebook.com", "m.facebook.com"}:
        return False
    return bool(
        re.fullmatch(
            r"/[^/]+/posts/pfbid[A-Za-z0-9]+/?",
            parsed.path,
            flags=re.IGNORECASE,
        )
    )


def is_facebook_share_navigation_url(url):
    """Recognize opaque Facebook share links as navigation inputs only."""
    value = str(url or "").strip()
    if not value:
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if host not in {"facebook.com", "www.facebook.com", "m.facebook.com"}:
        return False
    return bool(re.fullmatch(r"/share/(?:p|r|v)/[A-Za-z0-9._-]+/?", parsed.path, flags=re.IGNORECASE))


def extract_navigation_post_candidates(body):
    """Extract exact post/owner pairs from one trusted navigation response."""
    candidates = {}
    for document in decode_stream(body):
        for value in walk(document):
            if not isinstance(value, dict) or value.get("__typename") not in ("Story", "PartialStory"):
                continue
            feedback = value.get("feedback") or {}
            if not isinstance(feedback, dict):
                continue
            post_id = value.get("post_id") or value.get("legacy_api_post_id") or feedback.get("legacy_api_post_id")
            if not post_id:
                continue
            actor = first_actor(value)
            owner = feedback.get("owning_profile") or {}
            author_id = actor.get("id") or owner.get("id")
            if not author_id:
                continue
            legacy_story_id = value.get("legacy_api_story_id")
            key = (str(post_id), str(author_id))
            item = candidates.setdefault(
                key,
                {
                    "post_id": str(post_id),
                    "author_id": str(author_id),
                    "legacy_api_story_id": str(legacy_story_id) if legacy_story_id else None,
                },
            )
            if not item.get("legacy_api_story_id") and legacy_story_id:
                item["legacy_api_story_id"] = str(legacy_story_id)
    return list(candidates.values())


def extract_strict_navigation_identity(body):
    """Require every Story reference in one navigation response to agree."""
    identities = set()
    complete_identities = set()
    for document in decode_stream(body):
        for value in walk(document):
            if not isinstance(value, dict) or value.get("__typename") not in ("Story", "PartialStory"):
                continue
            feedback = value.get("feedback")
            feedback = feedback if isinstance(feedback, dict) else {}
            post_id = str(
                value.get("post_id")
                or value.get("legacy_api_post_id")
                or feedback.get("legacy_api_post_id")
                or ""
            )
            actor = first_actor(value)
            owner = feedback.get("owning_profile") or {}
            author_id = str(actor.get("id") or owner.get("id") or "")
            legacy_story_id = str(value.get("legacy_api_story_id") or "")
            story_identities = set()
            relay_identity = relay_story_identity(value.get("id"))
            if relay_identity:
                story_identities.add((relay_identity["post_id"], relay_identity["author_id"]))
            legacy_match = re.fullmatch(r"(\d{1,32})_(\d{1,32})", legacy_story_id)
            if legacy_match:
                story_identities.add((legacy_match.group(2), legacy_match.group(1)))
            if re.fullmatch(r"\d{1,32}", post_id) and re.fullmatch(r"\d{1,32}", author_id):
                story_identities.add((post_id, author_id))
            if len(story_identities) != 1:
                return None
            story_post_id, story_author_id = next(iter(story_identities))
            if post_id and post_id != story_post_id:
                return None
            if author_id and author_id != story_author_id:
                return None
            if legacy_story_id and legacy_story_id != f"{story_author_id}_{story_post_id}":
                return None
            identity = (story_post_id, story_author_id)
            identities.add(identity)
            if post_id and author_id and legacy_story_id:
                complete_identities.add(identity)
    if len(identities) != 1 or complete_identities != identities:
        return None
    post_id, author_id = next(iter(identities))
    return {
        "post_id": post_id,
        "author_id": author_id,
        "legacy_api_story_id": f"{author_id}_{post_id}",
    }


def is_search_discovery_query(friendly_name):
    name = str(friendly_name or "")
    return name.startswith(
        (
            "SearchResultsGraphQL",
            "Fb4aSearchTypeaheadQuery",
            "FB4ASearchTypeahead",
            "SearchSERP",
            "SearchTopPositionAdsQuery",
            "InitiateWarmSearchQuery",
            "FetchBootstrapKeywords",
        )
    )


def verify_target_posts(posts, expected_post_id=None, expected_url=None):
    expected_post_id = str(expected_post_id or "").strip()
    observed_ids = sorted({str(row.get("post_id")) for row in posts if row.get("post_id")})
    if expected_post_id and expected_post_id in observed_ids:
        return {
            "status": "matched",
            "matched_by": "post_id",
            "expected_post_id": expected_post_id,
            "expected_url": expected_url or None,
            "expected_url_tokens": target_url_tokens(expected_url),
            "observed_post_ids": observed_ids,
        }

    expected_tokens = target_url_tokens(expected_url)
    for row in posts:
        observed_url = str(row.get("url") or "")
        if not observed_url:
            continue
        if expected_post_id and str(row.get("post_id") or "").strip() != expected_post_id:
            continue
        if _url_component_match(expected_url, observed_url):
            return {
                "status": "matched",
                "matched_by": "url-token",
                "matched_token": _matched_url_token(expected_url, observed_url),
                "expected_post_id": expected_post_id or None,
                "expected_url": expected_url or None,
                "expected_url_tokens": expected_tokens,
                "observed_post_ids": observed_ids,
            }
    return {
        "status": "not-matched",
        "matched_by": None,
        "expected_post_id": expected_post_id or None,
        "expected_url": expected_url or None,
        "expected_url_tokens": expected_tokens,
        "observed_post_ids": observed_ids,
    }


def verify_target_capture(posts, records, expected_post_id=None, expected_url=None):
    # The aggregate `posts` list can contain stories rendered by Facebook search.
    # Discovery is not navigation: exact target-open verification must require a
    # non-search response generated after the opening action.
    result = verify_target_posts([], expected_post_id, expected_url)

    expected_post_id = str(expected_post_id or "").strip()
    expected_tokens = target_url_tokens(expected_url)
    expected_author_id = target_url_author_id(expected_url)
    expected_pfbid_url = is_facebook_pfbid_post_url(expected_url)
    share_navigation = is_facebook_share_navigation_url(expected_url)
    expected_url_hash = target_url_digest(expected_url)
    expected_navigation_hashes = facebook_navigation_url_digests(expected_url)
    expected_reel_match = re.fullmatch(
        r"/reel/(\d{1,32})/?",
        urlsplit(str(expected_url or "")).path,
        flags=re.IGNORECASE,
    )
    expected_reel_id = expected_reel_match.group(1) if expected_reel_match else None
    expected_permalink_match = re.fullmatch(
        r"/(?:groups/\d+/)?permalink/(\d{1,32})/?",
        urlsplit(str(expected_url or "")).path,
        flags=re.IGNORECASE,
    )
    expected_permalink_id = expected_permalink_match.group(1) if expected_permalink_match else None
    share_reel_navigation = bool(
        share_navigation
        and re.fullmatch(r"/share/r/[A-Za-z0-9._-]+/?", urlsplit(str(expected_url)).path, flags=re.IGNORECASE)
    )
    resolver_story_id = None
    for line_number, record in records:
        if str(record.get("status_code")) != "200":
            continue
        friendly = record.get("friendly_name") or record.get("request_headers", {}).get("x-fb-friendly-name")
        if not friendly:
            continue
        # Search-result payloads prove only that Facebook found the ID, not that
        # the App actually navigated to that post.  Treating them as target-open
        # proof caused the search EditText/result screen to be accepted as a post.
        if is_search_discovery_query(friendly):
            continue
        body = str(record.get("response_body") or "")
        if not body:
            continue
        documents = decode_stream(body)
        # An opaque pfbid vanity URL is accepted only through the exact
        # navigation request and one complete Story identity.  Keep weaker
        # response-body fallbacks out of this path.
        if expected_pfbid_url:
            if (
                friendly != "FBVideoHomeExternalPostLinkQuery"
                or record.get("request_navigation_url_hash") not in expected_navigation_hashes
            ):
                continue
            identity = extract_strict_navigation_identity(body)
            if identity is None:
                continue
            post_id = identity["post_id"]
            author_id = identity["author_id"]
            legacy_story_id = identity["legacy_api_story_id"]
            if expected_post_id and post_id != expected_post_id:
                continue
            result.update(
                {
                    "status": "matched",
                    "matched_by": "pfbid-external-post-link-navigation",
                    "matched_line": line_number,
                    "matched_friendly_name": friendly,
                    "matched_post_id": post_id,
                    "resolved_author_id": author_id,
                    "matched_legacy_api_story_id": legacy_story_id,
                }
            )
            return result
        if (
            share_navigation
            and friendly == "GetURLResolverInfo"
            and record.get("request_navigation_url_hash") == expected_url_hash
        ):
            resolver_story_id = _url_resolver_story_id(body)
            continue
        if share_navigation and friendly == "StaticGraphQlStoryFeedbackAtConnectionQuery" and resolver_story_id:
            static_story_id = _static_story_id(body)
            if static_story_id == resolver_story_id:
                identity = relay_story_identity(static_story_id)
                if identity and (not expected_post_id or identity["post_id"] == expected_post_id):
                    result.update(
                        {
                            "status": "matched",
                            "matched_by": "share-url-resolver-story",
                            "matched_line": line_number,
                            "matched_friendly_name": friendly,
                            "matched_post_id": identity["post_id"],
                            "expected_url_author_id": identity["author_id"],
                            "resolved_author_id": identity["author_id"],
                        }
                    )
                    return result
        if share_reel_navigation and friendly == "ReelsVDDChainingQuery":
            identity = _reel_seed_story_identity(body, record.get("request_seed_video_id"))
            if identity and (not expected_post_id or identity["post_id"] == expected_post_id):
                result.update(
                    {
                        "status": "matched",
                        "matched_by": "share-reel-seed-story",
                        "matched_line": line_number,
                        "matched_friendly_name": friendly,
                        "matched_post_id": identity["post_id"],
                        "expected_url_author_id": identity["author_id"],
                        "resolved_author_id": identity["author_id"],
                    }
                )
                return result
        if share_navigation and friendly in {"PermalinkSectionGraphQL", "FBVideoHomeExternalPostLinkQuery"}:
            candidates = extract_navigation_post_candidates(body)
            unique_post_ids = sorted({item["post_id"] for item in candidates})
            if len(unique_post_ids) == 1:
                resolved_post_id = unique_post_ids[0]
                matching_candidates = [item for item in candidates if item["post_id"] == resolved_post_id]
                author_ids = sorted({item["author_id"] for item in matching_candidates if item.get("author_id")})
                if len(author_ids) == 1 and (not expected_post_id or resolved_post_id == expected_post_id):
                    legacy_ids = sorted({item["legacy_api_story_id"] for item in matching_candidates if item.get("legacy_api_story_id")})
                    result.update(
                        {
                            "status": "matched",
                            "matched_by": "share-navigation-graphql",
                            "matched_line": line_number,
                            "matched_friendly_name": friendly,
                            "matched_post_id": resolved_post_id,
                            "expected_url_author_id": author_ids[0],
                            "resolved_author_id": author_ids[0],
                            "matched_legacy_api_story_id": legacy_ids[0] if len(legacy_ids) == 1 else None,
                        }
                    )
                    return result
        story_candidates = _post_identity_candidates(documents)
        if expected_reel_id:
            mappings = _reel_video_story_mappings(documents, expected_reel_id)
            if len(mappings) == 1 and (
                not expected_post_id or mappings[0]["post_id"] == expected_post_id
            ):
                result.update(
                    {
                        "status": "matched",
                        "matched_by": "reel-video-story-mapping",
                        "matched_line": line_number,
                        "matched_friendly_name": friendly,
                        "matched_post_id": mappings[0]["post_id"],
                        "resolved_author_id": mappings[0]["author_id"],
                    }
                )
                return result
        exact_rows = _story_candidates_for_post(story_candidates, expected_post_id, expected_url)
        if expected_post_id and exact_rows:
            exact_author_ids = sorted({row["author_id"] for row in exact_rows if row.get("author_id")})
            if len(exact_author_ids) > 1:
                continue
            result.update(
                {
                    "status": "matched",
                    "matched_by": "graphql-response-post-id",
                    "matched_line": line_number,
                    "matched_friendly_name": friendly,
                    "matched_post_id": expected_post_id,
                    "expected_url_author_id": exact_author_ids[0] if len(exact_author_ids) == 1 else expected_author_id,
                }
            )
            return result
        # Android external-link navigation can resolve an opaque pfbid to a
        # numeric post ID without echoing the pfbid in the response.  Treat that
        # as proof only for Facebook's dedicated ExternalPostLink query and only
        # when the response contains exactly one Story whose author is exactly
        # the author encoded in the requested URL.  Feed/preload queries cannot
        # satisfy this rule.
        if expected_tokens and expected_author_id and friendly == "FBVideoHomeExternalPostLinkQuery":
            identity = _external_post_link_identity(
                story_candidates,
                expected_post_id,
                expected_author_id,
                expected_url,
            )
            if identity:
                result.update(
                    {
                        "status": "matched",
                        "matched_by": "external-post-link-author",
                        "matched_line": line_number,
                        "matched_friendly_name": friendly,
                        "matched_post_id": identity["post_id"],
                        "expected_url_author_id": expected_author_id,
                    }
                )
                return result
        if (
            expected_post_id
            and not expected_reel_id
            and not expected_permalink_id
            and (
                friendly == "StaticFeedbackBatchAtConnection"
                or is_reaction_query(friendly)
            )
        ):
            source_post_id = str(record_source_post_id(record) or "").strip()
            if source_post_id == expected_post_id:
                result.update(
                    {
                        "status": "matched",
                        "matched_by": "graphql-response-feedback-source",
                        "matched_line": line_number,
                        "matched_friendly_name": friendly,
                        "matched_post_id": expected_post_id,
                    }
                )
                return result
        decoded_urls = list(_decoded_url_values(documents))
        url_evidence = any(_url_component_match(expected_url, value) for value in decoded_urls)
        video_evidence = expected_reel_id and expected_reel_id in set(_decoded_video_ids(documents))
        if expected_post_id:
            if (
                (
                    (expected_reel_id and video_evidence)
                    or (not expected_reel_id and (video_evidence or url_evidence))
                )
                and result.get("status") != "matched"
            ):
                result.update(
                    {
                        "status": "matched",
                        "matched_by": "graphql-response-url-token",
                        "matched_token": expected_reel_id,
                        "matched_line": line_number,
                        "matched_friendly_name": friendly,
                    }
                )
                continue
            if expected_permalink_id and url_evidence:
                result.update(
                    {
                        "status": "matched",
                        "matched_by": "graphql-response-url-token",
                        "matched_token": next(
                            (
                                token
                                for token in expected_tokens
                                if any(
                                    token == _matched_url_token(expected_url, value)
                                    for value in decoded_urls
                                )
                            ),
                            None,
                        ),
                        "matched_line": line_number,
                        "matched_friendly_name": friendly,
                    }
                )
                continue
            continue
        story_evidence = bool(exact_rows) and _story_candidates_are_unambiguous(
            exact_rows,
            expected_author_id,
        )
        if (story_evidence or url_evidence or video_evidence) and result.get("status") != "matched":
            matched_token = expected_reel_id if video_evidence else next(
                (
                    token
                    for token in expected_tokens
                    if any(
                        token == _matched_url_token(expected_url, value)
                        for value in decoded_urls
                    )
                ),
                next(
                    (
                        token
                        for token in expected_tokens
                        if token in {item["post_id"] for item in exact_rows}
                    ),
                    None,
                ),
            )
            result.update(
                {
                    "status": "matched",
                    "matched_by": "graphql-response-url-token",
                    "matched_token": matched_token,
                    "matched_line": line_number,
                    "matched_friendly_name": friendly,
                }
            )
            if expected_reel_id or expected_permalink_id:
                continue
            return result
    if (
        result.get("status") == "matched"
        and (expected_reel_id or expected_permalink_id)
        and not result.get("matched_post_id")
    ):
        matched_line = int(result.get("matched_line") or 0)
        share_query_after_token = any(
            line_number > matched_line
            and (
                record.get("friendly_name")
                or record.get("request_headers", {}).get("x-fb-friendly-name")
                or ""
            ).startswith("ResharesOfContentFeedQuery")
            for line_number, record in records
        )
        feedback_source_ids = sorted(
            {
                str(record_source_post_id(record) or "").strip()
                for line_number, record in records
                if line_number > matched_line
                and (
                    record.get("friendly_name") == "StaticFeedbackBatchAtConnection"
                    or is_reaction_query(record.get("friendly_name"))
                    or (
                        record.get("friendly_name")
                        or record.get("request_headers", {}).get("x-fb-friendly-name")
                        or ""
                    ).startswith("ResharesOfContentFeedQuery")
                )
                and str(record_source_post_id(record) or "").strip().isdigit()
            }
        )
        source_matches_target = bool(
            len(feedback_source_ids) == 1
            and expected_permalink_id
            and feedback_source_ids[0] == expected_permalink_id
        )
        reel_reactor_source_ids = sorted(
            {
                str(record_source_post_id(record) or "").strip()
                for line_number, record in records
                if expected_reel_id
                and line_number > matched_line
                and str(record.get("status_code")) == "200"
                and (
                    record.get("friendly_name")
                    or record.get("request_headers", {}).get("x-fb-friendly-name")
                    or ""
                )
                == "FbShortsIGMediaReactorsQuery"
                and str(record_source_post_id(record) or "").strip().isdigit()
            }
        )
        reel_source_matches_target = bool(
            len(reel_reactor_source_ids) == 1
            and (
                not expected_post_id
                or reel_reactor_source_ids[0] == expected_post_id
            )
        )
        if reel_source_matches_target:
            result.update(
                {
                    "matched_by": "reel-video-token-and-feedback-source",
                    "matched_post_id": reel_reactor_source_ids[0],
                }
            )
        elif source_matches_target:
            result.update(
                {
                    "matched_by": "permalink-token-and-feedback-source",
                    "matched_post_id": feedback_source_ids[0],
                }
            )
        elif expected_post_id or expected_reel_id or share_query_after_token:
            # A Reel video's URL token is not its Story post ID.  A conflicting
            # explicit ID or ambiguous post-token Share source must not promote
            # the provisional video-token match into target proof.
            result = verify_target_posts([], expected_post_id, expected_url)
    return result


def iter_records(path, max_records, min_line=1):
    errors = 0
    records = []
    record_limit = max(0, int(max_records or 0))
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if line_number < max(1, int(min_line or 1)):
                continue
            if record_limit and len(records) >= record_limit:
                break
            try:
                records.append((line_number, json.loads(line)))
            except json.JSONDecodeError:
                errors += 1
    return records, errors


def decode_stream(body):
    if not body:
        return []
    decoder = json.JSONDecoder()
    index = 0
    documents = []
    while index < len(body):
        while index < len(body) and body[index].isspace():
            index += 1
        if index >= len(body):
            break
        try:
            value, end = decoder.raw_decode(body, index)
        except json.JSONDecodeError:
            return documents
        documents.append(value)
        index = end
    return documents


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def text_value(value):
    if isinstance(value, dict):
        return value.get("text") or value.get("title") or ""
    return value or ""


def first_actor(value):
    actors = value.get("actors") or [] if isinstance(value, dict) else []
    for actor in actors:
        if isinstance(actor, dict) and (actor.get("name") or actor.get("id")):
            return actor
    return {}


def feedback_counts(feedback):
    comments = feedback.get("comments_api_count") or {}
    comments = (comments.get("comments") or {}).get("total_count")
    if comments is None:
        comments = (feedback.get("top_level_comments") or {}).get("count")
    return {
        "comment_count": comments,
        "reaction_count": (feedback.get("reactors") or {}).get("count"),
        "like_count": (feedback.get("likers") or {}).get("count"),
        "share_count": (feedback.get("reshares") or {}).get("count"),
        "top_reactions": [
            {
                "reaction_id": (edge.get("node") or {}).get("id"),
                "count": edge.get("reaction_count"),
            }
            for edge in ((feedback.get("top_reactions") or {}).get("edges") or [])
        ],
    }


def parse_query(body):
    parsed = parse_qs(body or "", keep_blank_values=True)
    variables = {}
    raw_variables = parsed.get("variables", [""])[0]
    if raw_variables:
        try:
            variables = json.loads(raw_variables)
        except json.JSONDecodeError:
            pass
    return parsed, variables


def response_feedback_post_id(record):
    """Return a source post only when one unique Feedback global ID is present."""
    friendly = str(record.get("friendly_name") or "") if isinstance(record, dict) else ""
    if not is_reaction_query(friendly):
        return None
    source_ids = set()
    for document in decode_stream(record.get("response_body") or ""):
        for value in walk(document):
            if not isinstance(value, dict):
                continue
            for key in ("id", "strong_id__"):
                candidate = value.get(key)
                if not isinstance(candidate, str):
                    continue
                post_id = feedback_post_id(candidate)
                if not post_id:
                    try:
                        padded = candidate + ("=" * (-len(candidate) % 4))
                        decoded = base64.b64decode(
                            padded.encode("ascii"), altchars=b"-_", validate=True
                        ).decode("utf-8")
                    except (binascii.Error, ValueError, UnicodeDecodeError, UnicodeEncodeError):
                        decoded = ""
                    group_feedback = re.fullmatch(r"feedback:([0-9]+)_[0-9]+", decoded)
                    if group_feedback and isinstance(value.get("associated_group"), dict):
                        post_id = group_feedback.group(1)
                if post_id:
                    source_ids.add(post_id)
                    if len(source_ids) > 1:
                        return None
    return next(iter(source_ids)) if len(source_ids) == 1 else None


def record_source_post_id(record):
    stored = record.get("request_feedback_post_id")
    if stored:
        return str(stored)
    stored = record.get("response_feedback_post_id")
    if stored:
        return str(stored)
    response_source = response_feedback_post_id(record)
    if response_source:
        return response_source
    _, variables = parse_query(record.get("request_body") or "")
    return feedback_post_id(variables.get("feedback_id"))


def record_original_content_id(record):
    stored = record.get("request_original_content_id")
    if stored:
        return str(stored)
    _, variables = parse_query(record.get("request_body") or "")
    for key in ("original_content_id", "originalContentId", "originalContentID"):
        value = variables.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def record_reaction_id(record):
    stored = record.get("request_reaction_id")
    if stored:
        return str(stored)
    _, variables = parse_query(record.get("request_body") or "")
    return variables.get("reaction_id")


def query_family_count(query_counts, prefix):
    return sum(count for name, count in query_counts.items() if name.startswith(prefix))


def query_family_source_ids(records, prefix):
    source_ids = set()
    for _, record in records:
        name = record.get("friendly_name") or record.get("request_headers", {}).get("x-fb-friendly-name") or ""
        if not name.startswith(prefix):
            continue
        source_post_id = record_source_post_id(record)
        if source_post_id:
            source_ids.add(str(source_post_id))
    return sorted(source_ids)


def query_family_count_for_source(records, prefix, source_post_id=None):
    expected = str(source_post_id or "").strip()
    count = 0
    for _, record in records:
        name = record.get("friendly_name") or record.get("request_headers", {}).get("x-fb-friendly-name") or ""
        if not name.startswith(prefix):
            continue
        if expected and str(record_source_post_id(record) or "") != expected:
            continue
        count += 1
    return count


def parse_display_count(value):
    if value in (None, ""):
        return None
    match = re.search(r"([0-9][0-9,\.\s]*)([KkMmBb]|千|万|萬|亿|億)?", str(value))
    if not match:
        return None
    raw = re.sub(r"[,\s]", "", match.group(1))
    try:
        number = float(raw)
    except ValueError:
        return None
    suffix = match.group(2) or ""
    multiplier = {
        "K": 1_000,
        "k": 1_000,
        "千": 1_000,
        "M": 1_000_000,
        "m": 1_000_000,
        "万": 10_000,
        "萬": 10_000,
        "B": 1_000_000_000,
        "b": 1_000_000_000,
        "亿": 100_000_000,
        "億": 100_000_000,
    }.get(suffix, 1)
    return int(round(number * multiplier))


def diagnostic_visible_count(action):
    if not isinstance(action, dict):
        return None
    candidate = action.get("candidate") or {}
    if not isinstance(candidate, dict):
        return None
    return parse_display_count(candidate.get("label"))


def target_post_reported_count(posts, source_post_id, field):
    """Return a count only from the exact target post summary already returned by Facebook."""
    expected = str(source_post_id or "").strip()
    if not expected:
        return None
    for post in posts or []:
        if not isinstance(post, dict) or str(post.get("post_id") or "") != expected:
            continue
        value = post.get(field)
        if value in (None, "") or isinstance(value, bool):
            return None
        try:
            count = int(value)
        except (TypeError, ValueError):
            return None
        return count if count >= 0 else None
    return None


def enumeration_status(
    query_records,
    user_records,
    visibility_hint=None,
    total_count=None,
    terminal_page_seen=None,
):
    if query_records <= 0:
        return "query-not-triggered"
    if visibility_hint == "friends-only-names":
        if user_records > 0:
            return "friends-only-visible-users-enumerated"
        return "query-triggered-friends-only-visibility"
    if user_records > 0:
        if (
            total_count is not None
            and terminal_page_seen is True
            and user_records < total_count
        ):
            return "visible-users-enumerated-subset"
        return "visible-users-enumerated"
    return "query-triggered-no-visible-users"


def classify_share_visibility(
    action,
    query_records,
    user_records,
    total_count=None,
    terminal_page_seen=None,
    screening=None,
):
    """Classify share enumeration without treating UI/query failures as privacy."""
    action = action if isinstance(action, dict) else {}
    screening = screening if isinstance(screening, dict) else {}
    list_confirmed = bool(screening.get("list_confirmed")) or bool(action.get("confirmed"))
    entry_method = screening.get("entry_method") or action.get("entry_method") or None
    failure_reason = action.get("failure_reason") or ""

    if user_records > 0:
        if total_count is not None and terminal_page_seen is True and user_records < total_count:
            return {
                "classification": "visible-subset-terminal",
                "decision_reason": "visible-share-users-terminal-below-ui-total",
                "list_confirmed": list_confirmed,
                "entry_method": entry_method,
            }
        return {
            "classification": "enumerable",
            "decision_reason": "visible-share-users",
            "list_confirmed": list_confirmed,
            "entry_method": entry_method,
        }

    if query_records > 0 and terminal_page_seen is True:
        return {
            "classification": "no-visible-share-users",
            "decision_reason": "terminal-without-visible-share-users",
            "list_confirmed": list_confirmed,
            "entry_method": entry_method,
        }

    if failure_reason in ("no-visible-share-count-entry", "no-share-count-after-comments-normalization"):
        return {
            "classification": "no-share-entry",
            "decision_reason": failure_reason,
            "list_confirmed": False,
            "entry_method": entry_method,
        }

    if failure_reason == "share-list-not-confirmed":
        return {
            "classification": "entry-or-query-failed",
            "decision_reason": "share-list-not-confirmed",
            "list_confirmed": False,
            "entry_method": entry_method,
        }

    if list_confirmed and query_records == 0:
        return {
            "classification": "entry-or-query-failed",
            "decision_reason": "share-list-confirmed-query-not-triggered",
            "list_confirmed": True,
            "entry_method": entry_method,
        }

    if query_records > 0:
        return {
            "classification": "entry-or-query-failed",
            "decision_reason": "share-query-without-visible-users",
            "list_confirmed": list_confirmed,
            "entry_method": entry_method,
        }

    if screening.get("classification") == "no-share-entry":
        return {
            "classification": "no-share-entry",
            "decision_reason": screening.get("decision_reason") or "no-visible-share-count-entry",
            "list_confirmed": False,
            "entry_method": entry_method,
        }

    return {
        "classification": screening.get("classification") or "entry-or-query-failed",
        "decision_reason": screening.get("decision_reason") or "share-query-not-triggered",
        "list_confirmed": list_confirmed,
        "entry_method": entry_method,
    }


def load_ui_diagnostics(input_path):
    root = Path(input_path).parent
    path = root / "readonly-ui" / "exploration-diagnostics.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        value = {}
    value = value if isinstance(value, dict) else {}
    # Combined capture keeps the original Reaction diagnostics byte-for-byte.
    # Merge only Share semantic fields; an empty Share pagination_runs must not
    # erase the Reaction terminal/bounded-sample evidence. Standalone is unchanged.
    for directory in ("readonly-ui-share", "readonly-ui-share-fallback"):
        share_path = root / directory / "exploration-diagnostics.json"
        if not share_path.exists():
            continue
        share = json.loads(share_path.read_text(encoding="utf-8-sig"))
        if not isinstance(share, dict):
            raise ValueError(f"invalid combined Share diagnostics: {share_path}")
        actions = share.get("actions") or []
        if not isinstance(actions, list):
            raise ValueError(f"invalid combined Share actions: {share_path}")
        value["actions"] = list(value.get("actions") or []) + [
            action for action in actions if isinstance(action, dict) and action.get("action") == "share"
        ]
        for key in ("share_screening", "share_native_fallback"):
            if share.get(key) is not None:
                value[key] = share[key]
    return value


def latest_action_diagnostic(diagnostics, action):
    actions = diagnostics.get("actions") or [] if isinstance(diagnostics, dict) else []
    for item in reversed(actions):
        if isinstance(item, dict) and item.get("action") == action:
            return item
    return {}


def feedback_post_id(feedback_id):
    if not feedback_id:
        return None
    value = str(feedback_id).strip()
    if not value or len(value) % 4 == 1:
        return None
    padded = value + ("=" * (-len(value) % 4))
    try:
        decoded = base64.b64decode(
            padded.encode("ascii"), altchars=b"-_", validate=True
        ).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError, UnicodeEncodeError):
        return None
    match = re.fullmatch(r"feedback:([0-9]+)", decoded)
    return match.group(1) if match else None


def stable_id(value, redact):
    if not value:
        return None
    if not redact:
        return value
    import hashlib

    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


def extract_posts(documents, redact):
    posts = {}
    for document in documents:
        for value in walk(document):
            if not isinstance(value, dict):
                continue
            feedback = value.get("feedback") or {}
            post_id = (
                value.get("post_id")
                or value.get("legacy_api_post_id")
                or (feedback.get("legacy_api_post_id") if isinstance(feedback, dict) else None)
            )
            if not post_id or not isinstance(feedback, dict):
                continue
            if value.get("__typename") not in ("Story", "PartialStory"):
                continue
            actor = first_actor(value)
            owner = feedback.get("owning_profile") or {}
            message = text_value(value.get("message"))
            if not message:
                for nested_story in (value.get("story"), value.get("attached_story")):
                    if isinstance(nested_story, dict):
                        message = text_value(nested_story.get("message"))
                        if message:
                            break
            key = str(post_id)
            posts[key] = {
                "post_id": stable_id(post_id, redact),
                "author_id": stable_id(actor.get("id") or owner.get("id"), redact),
                "author_name": actor.get("name") or owner.get("name"),
                "message": re.sub(r"\s+", " ", str(message)).strip()[:1000],
                "url": value.get("url") or value.get("wwwURL") or feedback.get("url"),
                "creation_time": value.get("creation_time") or feedback.get("creation_time"),
                **feedback_counts(feedback),
            }
    return list(posts.values())


def extract_feedback_posts(documents, redact):
    """Extract source-post summaries from detail/comment responses."""
    posts = {}

    def merge(post_id, owner, story=None, feedback=None):
        if not post_id or "_" in str(post_id):
            return
        story = story or {}
        feedback = feedback or {}
        actor = first_actor(story) if isinstance(story, dict) else {}
        candidate = {
            "post_id": stable_id(post_id, redact),
            "author_id": stable_id(actor.get("id") or owner.get("id"), redact),
            "author_name": actor.get("name") or owner.get("name"),
            "message": re.sub(r"\s+", " ", str(text_value(story.get("message")))).strip()[:1000],
            "url": story.get("url") or story.get("wwwURL"),
            "creation_time": story.get("creation_time"),
            **feedback_counts(feedback),
        }
        previous = posts.get(str(post_id), {})
        for key, value in candidate.items():
            if value not in (None, "", [], {}):
                previous[key] = value
        posts[str(post_id)] = previous

    for document in documents:
        for value in walk(document):
            if not isinstance(value, dict):
                continue
            parent_story = value.get("parent_post_story")
            if isinstance(parent_story, dict):
                parent_id = parent_story.get("post_id")
                if not parent_id:
                    encoded_id = parent_story.get("id")
                    try:
                        decoded_id = base64.b64decode(encoded_id).decode("utf-8")
                        parts = decoded_id.split(":")
                        parent_id = parts[2] if len(parts) >= 4 else None
                    except (ValueError, TypeError, UnicodeDecodeError):
                        parent_id = None
                owner = first_actor(parent_story)
                merge(parent_id, owner, parent_story, {})
            post_id = value.get("legacy_api_post_id") or value.get("post_id")
            # Comment and reply feedback IDs are suffixed with an underscore;
            # only the root post can be the source of a share/reaction edge.
            if not post_id or "_" in str(post_id) or not isinstance(value.get("owning_profile"), dict):
                continue
            owner = value.get("owning_profile") or {}
            if not owner.get("id") and not owner.get("name"):
                continue
            feedback = value if "reactors" in value or "reshares" in value else {}
            merge(post_id, owner, {}, feedback)
    return posts


SHARE_PROVENANCE_FIELDS = (
    "target_input_url", "canonical_url", "post_type", "post_id", "owner_id",
    "feedback_id", "share_actor_id", "share_actor_name", "share_actor_url",
    "page_marker", "source", "captured_at",
)


def share_target_context(input_url, target, posts, redact=False):
    """Evidence-only Share context; never synthesize an owner or resolved URL."""
    parsed = urlsplit(str(input_url or ""))
    path = parsed.path
    post_type = None
    if (parsed.hostname or "").lower() in {"facebook.com", "www.facebook.com", "m.facebook.com"}:
        if re.match(r"/share/p/[^/]+/?$", path):
            post_type = "share/p"
        elif re.match(r"/(?:reel/[^/]+|share/r/[^/]+)/?$", path):
            post_type = "reel/share/r"
        elif re.match(r"/groups/[^/]+/(?:posts|permalink)/[^/]+/?$", path):
            post_type = "group-post"
        elif re.search(r"/posts/pfbid[A-Za-z0-9]+/?$", path):
            post_type = "pfbid-post"
        elif re.search(r"/posts/[0-9]+/?$", path):
            post_type = "numeric-post"
    matched = str(target.get("matched_post_id") or "") if target.get("status") == "matched" else ""
    post = next((p for p in posts if str(p.get("post_id") or "") == matched), {}) if matched else {}
    return {
        "target_input_url": None if redact else input_url or None,
        "canonical_url": None if redact else post.get("url") or None,
        "post_type": post_type,
        "post_id": matched or None,
        "owner_id": post.get("author_id") or None,
    }


def extract_shares(records, redact, share_context=None):
    shares = {}
    for line_number, record in records:
        name = (record.get("friendly_name") or record.get("request_headers", {}).get("x-fb-friendly-name"))
        if not name or not name.startswith("ResharesOfContentFeedQuery"):
            continue
        source_post_id = record_source_post_id(record)
        original_content_id = record_original_content_id(record)
        for document in decode_stream(record.get("response_body") or ""):
            for value in walk(document):
                if not isinstance(value, dict):
                    continue
                connection = value.get("reshares")
                if not isinstance(connection, dict) or not isinstance(connection.get("edges"), list):
                    continue
                for edge in connection["edges"]:
                    node = edge.get("node") or {}
                    actor = first_actor(node)
                    key = node.get("id") or node.get("legacy_api_story_id") or f"line-{line_number}-{len(shares)}"
                    shares[str(key)] = {
                        "actor_id": stable_id(actor.get("id") or actor.get("strong_id__"), redact),
                        "actor_name": actor.get("name"),
                        "source_post_id": stable_id(source_post_id, redact),
                        "original_content_id": stable_id(original_content_id, redact),
                        "share_story_id": stable_id(node.get("id"), redact),
                        "legacy_api_story_id": stable_id(node.get("legacy_api_story_id"), redact),
                        "creation_time": node.get("creation_time"),
                        "share_url": node.get("url") or node.get("wwwURL"),
                        "share_message": text_value(node.get("message")),
                    }
                    if share_context is not None:
                        row = shares[str(key)]
                        _, variables = parse_query(record.get("request_body") or "")
                        feedback_id = variables.get("feedback_id")
                        if str(feedback_post_id(feedback_id) or "") != str(source_post_id or ""):
                            feedback_id = None
                        if not feedback_id:
                            for field in ("id", "strong_id__"):
                                candidate = value.get(field)
                                if candidate and str(feedback_post_id(candidate) or "") == str(source_post_id or ""):
                                    feedback_id = candidate
                                    break
                        matches_context = str(share_context.get("post_id") or "") == str(row.get("source_post_id") or "")
                        row.update({
                            **share_context,
                            "post_id": row.get("source_post_id"),
                            "canonical_url": share_context.get("canonical_url") if matches_context else None,
                            "owner_id": share_context.get("owner_id") if matches_context else None,
                            "feedback_id": stable_id(feedback_id, redact),
                            "share_actor_id": row.get("actor_id"),
                            "share_actor_name": row.get("actor_name"),
                            "share_actor_url": None if redact else actor.get("url") or actor.get("wwwURL") or None,
                            "page_marker": f"capture-line:{line_number}",
                            "source": name,
                            "captured_at": record.get("timestamp") or None,
                        })
    return list(shares.values())


def summarize_share_integrity(records, source_post_id=None):
    """Keep failed/malformed share queries visible even when they yield no page."""
    result = dict(query_records=0, failed_query_records=0, unparsed_query_records=0,
                  unidentified_edges=0, last_query_line=0)
    for line, record in records:
        name = record.get("friendly_name") or record.get("request_headers", {}).get("x-fb-friendly-name") or ""
        if not name.startswith("ResharesOfContentFeedQuery"):
            continue
        if source_post_id and str(record_source_post_id(record) or "") != str(source_post_id):
            continue
        result["query_records"] += 1
        result["last_query_line"] = max(result["last_query_line"], line)
        values = [value for document in decode_stream(record.get("response_body") or "")
                  for value in walk(document) if isinstance(value, dict)]
        failed = (str(record.get("status_code")) != "200"
                  or record.get("response_graphql_error_code") or record.get("response_api_error_code")
                  or _record_is_reaction_rate_limited(record) or _record_is_temporary_feature_block(record)
                  or any(value.get("errors") for value in values))
        result["failed_query_records"] += int(bool(failed))
        connections = [value["reshares"] for value in values
                       if isinstance(value.get("reshares"), dict)
                       and isinstance(value["reshares"].get("edges"), list)]
        result["unparsed_query_records"] += int(not connections)
        for connection in connections:
            for edge in connection["edges"]:
                actor = first_actor(edge.get("node") or {}) if isinstance(edge, dict) else {}
                result["unidentified_edges"] += int(not any(actor.get(k) for k in ("id", "strong_id__", "name")))
    return result


def extract_share_pages(records, redact):
    pages = []
    seen = set()
    for line_number, record in records:
        name = (record.get("friendly_name") or record.get("request_headers", {}).get("x-fb-friendly-name"))
        if not name or not name.startswith("ResharesOfContentFeedQuery"):
            continue
        source_post_id = record_source_post_id(record)
        original_content_id = record_original_content_id(record)
        for document in decode_stream(record.get("response_body") or ""):
            for value in walk(document):
                if not isinstance(value, dict):
                    continue
                connection = value.get("reshares")
                if not isinstance(connection, dict) or not isinstance(connection.get("edges"), list):
                    continue
                page_info = connection.get("page_info") or {}
                key = (line_number, str(source_post_id), len(connection["edges"]), page_info.get("has_next_page"), bool(page_info.get("end_cursor")))
                if key in seen:
                    continue
                seen.add(key)
                pages.append(
                    {
                        "line": line_number,
                        "source_post_id": stable_id(source_post_id, redact),
                        "original_content_id": stable_id(original_content_id, redact),
                        "edge_count": len(connection["edges"]),
                        "has_next_page": page_info.get("has_next_page"),
                        "end_cursor_present": bool(page_info.get("end_cursor")),
                    }
                )
    return pages


def _reactor_actor(value):
    """Return a user-like node from the common GraphQL wrapper shapes."""
    if not isinstance(value, dict):
        return {}
    if value.get("id") or value.get("strong_id__") or value.get("name"):
        return value
    for key in ("actor", "user", "profile", "node"):
        actor = _reactor_actor(value.get(key))
        if actor:
            return actor
    return {}


def _reactor_nodes(connection):
    """Normalize edges, nodes, and direct user arrays into one node list."""
    if isinstance(connection, list):
        return [_reactor_actor(node) for node in connection]
    if not isinstance(connection, dict):
        return []
    nodes = []
    edges = connection.get("edges")
    if isinstance(edges, list):
        for edge in edges:
            if isinstance(edge, dict):
                actor = _reactor_actor(edge.get("node") or edge.get("actor") or edge)
                edge_reaction = edge.get("feedback_reaction_info")
                if actor and isinstance(edge_reaction, dict) and not actor.get("feedback_reaction_info"):
                    actor = dict(actor)
                    actor["feedback_reaction_info"] = edge_reaction
                nodes.append(actor)
    direct_nodes = connection.get("nodes")
    if isinstance(direct_nodes, list):
        nodes.extend(_reactor_actor(node) for node in direct_nodes)
    if not nodes and (connection.get("id") or connection.get("strong_id__") or connection.get("name")):
        nodes.append(_reactor_actor(connection))
    return [node for node in nodes if node]


def _reactor_avatar_url(node):
    """Read an avatar URI only when Facebook already returned it on the reactor actor."""
    if not isinstance(node, dict):
        return None
    direct_keys = (
        "profile_picture_url",
        "profile_picture_uri",
        "profile_pic_url",
        "profile_pic_uri",
    )
    for key in direct_keys:
        value = node.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    for key in ("profile_picture", "profilePicture", "profile_pic", "picture"):
        value = node.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
        if not isinstance(value, dict):
            continue
        candidates = [value, value.get("data"), value.get("image")]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            for field in ("uri", "url"):
                uri = candidate.get(field)
                if isinstance(uri, str) and uri.startswith(("http://", "https://")):
                    return uri
    return None


def extract_reactors(records, redact):
    reactors = {}
    reaction_pages = []
    reactor_fields = ("reactors", "unified_reactors", "important_reactors", "friend_reactors")
    for line_number, record in records:
        name = (record.get("friendly_name") or record.get("request_headers", {}).get("x-fb-friendly-name"))
        if not is_reaction_query(name):
            continue
        source_post_id = record_source_post_id(record)
        original_content_id = record_original_content_id(record)
        reaction_id = record_reaction_id(record)
        for document in decode_stream(record.get("response_body") or ""):
            for value in walk(document):
                if not isinstance(value, dict):
                    continue
                for source_field in reactor_fields:
                    if source_field not in value:
                        continue
                    connection = value.get(source_field)
                    page_info = connection.get("page_info") or {} if isinstance(connection, dict) else {}
                    nodes = _reactor_nodes(connection)
                    if isinstance(connection, dict) and (
                        "edges" in connection or "nodes" in connection or page_info
                    ):
                        reaction_pages.append(
                            {
                                "line": line_number,
                                "source_post_id": stable_id(source_post_id, redact),
                                "original_content_id": stable_id(original_content_id, redact),
                                "reaction_id": reaction_id,
                                "source_field": source_field,
                                "edge_count": len(connection.get("edges") or []),
                                "node_count": len(nodes),
                                "has_next_page": page_info.get("has_next_page"),
                                "end_cursor_present": bool(page_info.get("end_cursor")),
                            }
                        )
                    for node in nodes:
                        raw_user_id = node.get("id") or node.get("strong_id__")
                        raw_user_name = node.get("name")
                        if not raw_user_id and not raw_user_name:
                            continue
                        node_reaction_info = node.get("feedback_reaction_info")
                        node_reaction_id = None
                        if isinstance(node_reaction_info, dict):
                            raw_node_reaction_id = node_reaction_info.get("id")
                            if raw_node_reaction_id not in (None, ""):
                                node_reaction_id = str(raw_node_reaction_id)
                        effective_reaction_id = node_reaction_id or reaction_id
                        raw_user_key = raw_user_id or f"name:{raw_user_name}"
                        key = (str(source_post_id or ""), str(raw_user_key))
                        row = {
                            "line": line_number,
                            "user_id": stable_id(raw_user_id, redact),
                            "user_name": raw_user_name,
                            "avatar_url": _reactor_avatar_url(node),
                            "source_post_id": stable_id(source_post_id, redact),
                            "original_content_id": stable_id(original_content_id, redact),
                            "reaction_id": stable_id(effective_reaction_id, redact) if effective_reaction_id else None,
                            "source_field": source_field,
                            "page_has_next_page": page_info.get("has_next_page"),
                            "page_end_cursor_present": bool(page_info.get("end_cursor")),
                        }
                        previous = reactors.get(key)
                        if previous is None:
                            reactors[key] = row
                            continue
                        for field in (
                            "user_id",
                            "user_name",
                            "avatar_url",
                            "source_post_id",
                            "original_content_id",
                            "reaction_id",
                        ):
                            if not previous.get(field) and row.get(field):
                                previous[field] = row[field]
    return list(reactors.values()), reaction_pages


def extract_reaction_counts(records, redact):
    rows = []
    for line_number, record in records:
        name = (record.get("friendly_name") or record.get("request_headers", {}).get("x-fb-friendly-name"))
        if name != "FetchFeedbackReactionsCountsQuery":
            continue
        for document in decode_stream(record.get("response_body") or ""):
            for value in walk(document):
                if not isinstance(value, dict) or not isinstance(value.get("top_reactions"), dict):
                    continue
                for edge in (value["top_reactions"].get("edges") or []):
                    rows.append(
                        {
                            "line": line_number,
                            "feedback_id": stable_id(value.get("id"), redact),
                            "reaction_id": (edge.get("node") or {}).get("id"),
                            "count": edge.get("reaction_count"),
                        }
                    )
    unique = {(row["line"], row["reaction_id"]): row for row in rows}
    return list(unique.values())


def summarize_reaction_page_groups(reaction_pages):
    groups = {}
    for page in reaction_pages:
        reaction_id = page.get("reaction_id")
        key = str(reaction_id) if reaction_id not in (None, "") else "__ALL__"
        group = groups.setdefault(
            key,
            {
                "reaction_id": reaction_id,
                "page_count": 0,
                "node_count": 0,
                "edge_count": 0,
                "open_page_seen": False,
                "terminal_page_seen": False,
            },
        )
        group["page_count"] += 1
        group["node_count"] += int(page.get("node_count") or 0)
        group["edge_count"] += int(page.get("edge_count") or 0)
        if page.get("has_next_page") is True:
            group["open_page_seen"] = True
        elif page.get("has_next_page") is False:
            group["terminal_page_seen"] = True
    open_groups = sorted(
        key for key, group in groups.items()
        if group["open_page_seen"] and not group["terminal_page_seen"]
    )
    terminal_groups = sorted(key for key, group in groups.items() if group["terminal_page_seen"])
    return {
        "groups": groups,
        "open_groups": open_groups,
        "terminal_groups": terminal_groups,
        "open_group_count": len(open_groups),
        "terminal_group_count": len(terminal_groups),
        "all_observed_groups_terminal": bool(groups) and not open_groups,
    }


def _reaction_record_matches_source(record, source_post_id=None):
    name = record.get("friendly_name") or record.get("request_headers", {}).get("x-fb-friendly-name") or ""
    if not is_reaction_query(name):
        return False
    expected = str(source_post_id or "").strip()
    return not expected or str(record_source_post_id(record) or "") == expected


def _record_is_reaction_rate_limited(record):
    if bool(record.get("response_rate_limited")) or record.get("response_graphql_error_code") == 1675004:
        return True
    body = str(record.get("response_body") or "")
    return bool(re.search(r'"code"\s*:\s*1675004', body)) or "rate limit exceeded" in body.lower()


def _record_is_temporary_feature_block(record):
    if bool(record.get("response_temporary_feature_block")):
        return True
    if record.get("response_graphql_error_code") == 3252001 or record.get("response_api_error_code") == 368:
        return True
    body = str(record.get("response_body") or "")
    return bool(re.search(r'"api_error_code"\s*:\s*368', body)) or bool(
        re.search(r'"code"\s*:\s*3252001', body)
    )


def summarize_reaction_integrity(records, reactors, reaction_pages, reported_total_count=None, source_post_id=None):
    """Audit traversal completeness separately from visible-user dedupe counts."""
    matching_records = [
        (line, record)
        for line, record in records
        if _reaction_record_matches_source(record, source_post_id)
    ]
    rate_limited_records = [
        line for line, record in matching_records
        if _record_is_reaction_rate_limited(record)
    ]
    session_restriction_records = [
        (line, record) for line, record in records
        if _record_is_temporary_feature_block(record)
    ]
    raw_reactor_rows = sum(int(page.get("node_count") or 0) for page in reaction_pages)
    unique_reactor_identities = len(reactors)
    duplicate_rows = max(0, raw_reactor_rows - unique_reactor_identities)
    successful_page_lines = sorted({int(page.get("line") or 0) for page in reaction_pages if page.get("line")})
    if successful_page_lines:
        session_restriction_records = [
            (line, record)
            for line, record in session_restriction_records
            if is_reaction_query(
                record.get("friendly_name")
                or record.get("request_headers", {}).get("x-fb-friendly-name")
            )
        ]
    session_restriction_lines = [line for line, _ in session_restriction_records]
    page_summary = summarize_reaction_page_groups(reaction_pages)
    all_pages = [page for page in reaction_pages if page.get("reaction_id") in (None, "")]
    tail_pages = all_pages or list(reaction_pages)
    last_page = max(tail_pages, key=lambda page: int(page.get("line") or 0)) if tail_pages else None
    last_successful_page_has_next = last_page.get("has_next_page") if last_page else None
    if all_pages:
        terminal_page_seen = any(page.get("has_next_page") is False for page in all_pages)
    else:
        terminal_page_seen = bool(page_summary.get("all_observed_groups_terminal"))
    open_tail_without_terminal = bool(
        last_page
        and last_successful_page_has_next is True
        and not terminal_page_seen
    )
    total_gap = None
    if reported_total_count is not None:
        try:
            total_gap = int(reported_total_count) - unique_reactor_identities
        except (TypeError, ValueError):
            total_gap = None

    if session_restriction_lines and not terminal_page_seen:
        integrity_status = "session-restricted"
    elif rate_limited_records and not terminal_page_seen:
        integrity_status = "rate-limited"
    elif terminal_page_seen:
        integrity_status = (
            "complete-terminal-visible-subset"
            if total_gap is not None and total_gap > 0
            else "complete"
        )
    elif open_tail_without_terminal:
        integrity_status = "partial-open-tail"
    elif reaction_pages:
        integrity_status = "partial-no-terminal"
    elif rate_limited_records:
        integrity_status = "rate-limited"
    elif matching_records:
        integrity_status = "no-pages"
    else:
        integrity_status = "query-not-triggered"

    return {
        "integrity_status": integrity_status,
        "query_records": len(matching_records),
        "connection_page_count": len(reaction_pages),
        "successful_page_count": len(successful_page_lines),
        "raw_reactor_rows": raw_reactor_rows,
        "unique_reactor_identities": unique_reactor_identities,
        "duplicate_rows": duplicate_rows,
        "rate_limited_records": len(rate_limited_records),
        "rate_limited_lines": rate_limited_records,
        "temporary_feature_block_records": len(session_restriction_lines),
        "temporary_feature_block_lines": session_restriction_lines,
        "terminal_page_seen": terminal_page_seen,
        "open_tail_without_terminal": open_tail_without_terminal,
        "last_successful_page_has_next": last_successful_page_has_next,
        "reported_total_count": reported_total_count,
        "reported_total_unique_gap": total_gap,
        "partial_data_valid": unique_reactor_identities > 0,
        "completion_claim_allowed": integrity_status in {"complete", "complete-terminal-visible-subset"},
    }


def write_csv(path, rows, fieldnames):
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main():
    args = parse_args()
    if not args.input.exists():
        raise SystemExit(f"input not found: {args.input}")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    records, parse_errors = iter_records(args.input, args.max_records, args.min_line)
    query_counts = Counter()
    hosts = Counter()
    statuses = Counter()
    graph_records = []
    documents = []
    for line_number, record in records:
        hosts[record.get("host") or ""] += 1
        statuses[str(record.get("status_code"))] += 1
        name = record.get("friendly_name") or record.get("request_headers", {}).get("x-fb-friendly-name") or "(unnamed)"
        query_counts[name] += 1
        if str(record.get("path") or record.get("url") or "").endswith("/graphql") or name != "(unnamed)":
            graph_records.append(record)
            documents.extend(decode_stream(record.get("response_body") or ""))

    posts = extract_posts(documents, args.redact_ids)
    post_by_id = {post["post_id"]: post for post in posts if post.get("post_id")}
    source_post_ids = set()
    for _, record in records:
        name = record.get("friendly_name") or record.get("request_headers", {}).get("x-fb-friendly-name")
        if not name or not (name.startswith("ResharesOfContentFeedQuery") or is_reaction_query(name)):
            continue
        source_post_id = record_source_post_id(record)
        if source_post_id:
            source_post_ids.add(stable_id(source_post_id, args.redact_ids))
    feedback_posts = extract_feedback_posts(documents, args.redact_ids)
    for post_id in source_post_ids:
        detail = feedback_posts.get(post_id)
        if detail is None:
            continue
        existing = post_by_id.setdefault(post_id, {})
        for key, value in detail.items():
            if value not in (None, "", [], {}):
                existing[key] = value
    posts = list(post_by_id.values())
    target_verification = verify_target_capture(posts, records, args.expected_post_id, args.expected_url)
    share_context = (
        share_target_context(args.share_target_input_url, target_verification, posts, args.redact_ids)
        if args.share_target_input_url else None
    )
    share_records = [(line, record) for line, record in records if line >= max(1, args.share_min_line)]
    shares = extract_shares(share_records, args.redact_ids, share_context)
    share_pages = extract_share_pages(share_records, args.redact_ids)
    reactors, reaction_pages = extract_reactors(records, args.redact_ids)
    focus_source_post_id = str(args.focus_source_post_id or "").strip()
    if focus_source_post_id:
        shares = [row for row in shares if str(row.get("source_post_id") or "") == focus_source_post_id]
        share_pages = [row for row in share_pages if str(row.get("source_post_id") or "") == focus_source_post_id]
        reactors = [row for row in reactors if str(row.get("source_post_id") or "") == focus_source_post_id]
        reaction_pages = [row for row in reaction_pages if str(row.get("source_post_id") or "") == focus_source_post_id]
    reaction_page_summary = summarize_reaction_page_groups(reaction_pages)
    reaction_counts = extract_reaction_counts(records, args.redact_ids)
    ui_diagnostics = load_ui_diagnostics(args.input)
    share_ui = latest_action_diagnostic(ui_diagnostics, "share")
    reaction_ui = latest_action_diagnostic(ui_diagnostics, "reaction")
    share_screening = ui_diagnostics.get("share_screening") or {}
    share_ui_total_count = diagnostic_visible_count(share_ui)
    reaction_ui_total_count = diagnostic_visible_count(reaction_ui)
    exact_target_post_id = focus_source_post_id or str(args.expected_post_id or "").strip()
    share_reported_total_count = share_ui_total_count
    share_reported_total_source = "ui-entry-label" if share_ui_total_count is not None else None
    if share_reported_total_count is None:
        share_reported_total_count = target_post_reported_count(posts, exact_target_post_id, "share_count")
        if share_reported_total_count is not None:
            share_reported_total_source = "target-post-feedback"
    reaction_reported_total_count = reaction_ui_total_count
    reaction_reported_total_source = "ui-entry-label" if reaction_ui_total_count is not None else None
    if reaction_reported_total_count is None:
        reaction_reported_total_count = target_post_reported_count(posts, exact_target_post_id, "reaction_count")
        if reaction_reported_total_count is not None:
            reaction_reported_total_source = "target-post-feedback"
    reaction_integrity = summarize_reaction_integrity(
        records,
        reactors,
        reaction_pages,
        reported_total_count=reaction_reported_total_count,
        source_post_id=focus_source_post_id,
    )
    reaction_visibility_hint = reaction_ui.get("visibility_hint") or None
    share_query_records = query_family_count_for_source(share_records, "ResharesOfContentFeedQuery", focus_source_post_id)
    reactor_query_records = query_family_count_for_source(records, REACTION_QUERY_PREFIXES, focus_source_post_id)
    share_terminal_page_seen = any(page.get("has_next_page") is False for page in share_pages)
    share_visibility = classify_share_visibility(
        share_ui,
        share_query_records,
        len(shares),
        total_count=share_reported_total_count,
        terminal_page_seen=share_terminal_page_seen,
        screening=share_screening,
    )
    page_info_count = sum(1 for document in documents for value in walk(document) if isinstance(value, dict) and "page_info" in value)
    stream_response_count = sum(1 for record in graph_records if len(decode_stream(record.get("response_body") or "")) > 1)
    report = {
        "input": str(args.input),
        "min_line": max(1, int(args.min_line or 1)),
        "focus_source_post_id": focus_source_post_id or None,
        "records_read": len(records),
        "line_parse_errors": parse_errors,
        "hosts": dict(hosts),
        "statuses": dict(statuses),
        "friendly_name_counts": dict(query_counts.most_common()),
        "graphql_records": len(graph_records),
        "streaming_graphql_responses": stream_response_count,
        "page_info_objects": page_info_count,
        "post_records": len(posts),
        "target_verification": target_verification,
        "source_post_ids_by_family": {
            "shares": query_family_source_ids(share_records, "ResharesOfContentFeedQuery"),
            "reactions": query_family_source_ids(records, REACTION_QUERY_PREFIXES),
        },
        "share_user_records": len(shares),
        "share_min_line": max(1, args.share_min_line),
        "share_integrity": summarize_share_integrity(share_records, focus_source_post_id),
        "reactor_user_records": len(reactors),
        "reaction_integrity": reaction_integrity,
        "reaction_count_rows": len(reaction_counts),
        "enumeration_status": {
            "shares": {
                "query_records": share_query_records,
                "status": enumeration_status(
                    share_query_records,
                    len(shares),
                    total_count=share_reported_total_count,
                    terminal_page_seen=share_terminal_page_seen,
                ),
                "terminal_page_seen": share_terminal_page_seen,
                "classification": share_visibility["classification"],
                "decision_reason": share_visibility["decision_reason"],
                "list_confirmed": share_visibility["list_confirmed"],
                "entry_method": share_visibility["entry_method"],
                "ui_total_count": share_ui_total_count,
                "reported_total_count": share_reported_total_count,
                "reported_total_source": share_reported_total_source,
                "enumerated_identity_count": len(shares),
                "identity_count_gap": (
                    share_reported_total_count - len(shares)
                    if share_reported_total_count is not None and share_reported_total_count >= len(shares)
                    else None
                ),
            },
            "reactions": {
                "query_records": reactor_query_records,
                "status": enumeration_status(
                    reactor_query_records,
                    len(reactors),
                    reaction_visibility_hint,
                    total_count=reaction_reported_total_count,
                    terminal_page_seen=bool((reaction_page_summary.get("groups") or {}).get("__ALL__", {}).get("terminal_page_seen")),
                ),
                "ui_visibility_hint": reaction_visibility_hint,
                "ui_total_count": reaction_ui_total_count,
                "reported_total_count": reaction_reported_total_count,
                "reported_total_source": reaction_reported_total_source,
                "enumerated_identity_count": len(reactors),
                "identity_count_gap": (
                    reaction_reported_total_count - len(reactors)
                    if reaction_reported_total_count is not None and reaction_reported_total_count >= len(reactors)
                    else None
                ),
                "pagination": reaction_page_summary,
            },
        },
        "ui_diagnostics": {
            "share": share_ui,
            "reaction": reaction_ui,
        },
        "share_pages": share_pages,
        "reactor_pages": reaction_pages,
        "reactor_source_fields": sorted({page["source_field"] for page in reaction_pages}),
        "observed_limits": {
            "reactor_edges_empty": bool(reaction_pages) and sum(p["edge_count"] for p in reaction_pages) == 0,
            "share_records_are_visible_subset": True,
            "no_request_replay_performed": True,
        },
        "posts": posts[:100],
    }
    (args.out_dir / "facebook-sample-patterns.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(args.out_dir / "facebook-posts.csv", posts, list(posts[0].keys()) if posts else ["post_id", "author_id", "author_name", "message", "url", "creation_time"])
    write_csv(args.out_dir / "facebook-share-users.csv", shares, ["actor_id", "actor_name", "source_post_id", "original_content_id", "share_story_id", "legacy_api_story_id", "creation_time", "share_url", "share_message"] + (list(SHARE_PROVENANCE_FIELDS) if share_context is not None else []))
    write_csv(
        args.out_dir / "facebook-reactor-users.csv",
        reactors,
        [
            "line",
            "user_id",
            "user_name",
            "avatar_url",
            "source_post_id",
            "original_content_id",
            "reaction_id",
            "source_field",
            "page_has_next_page",
            "page_end_cursor_present",
        ],
    )
    write_csv(args.out_dir / "facebook-reaction-counts.csv", reaction_counts, ["line", "feedback_id", "reaction_id", "count"])
    lines = [
        "# Facebook 样本规律报告",
        "",
        f"输入：`{args.input}`",
        f"读取记录：{len(records)}，JSONL 解析失败：{parse_errors}",
        f"GraphQL 记录：{len(graph_records)}，连续 JSON 响应：{stream_response_count}",
        f"帖子摘要：{len(posts)}，可见转发用户：{len(shares)}，可见互动用户：{len(reactors)}",
        f"分页对象：{page_info_count}，互动分页请求：{len(reaction_pages)}",
        "",
        "## 关键查询",
        "",
    ]
    for name, count in query_counts.most_common(25):
        lines.append(f"- `{name}`: {count}")
    lines.extend(
        [
            "",
            "## 解释",
            "",
            "- 信息流和帖子详情使用 GraphQL；部分响应是连续 JSON 文档流。",
            "- 转发用户从 `ResharesOfContentFeedQuery` 的 `reshares.edges[].node.actors[]` 提取。",
            "- 互动用户名单从 `FeedbackReactorsGraphService` 或 Reel 的 `FbShortsIGMediaReactorsQuery` 连接结构提取，并按源帖和用户去重。空边和空节点记录为当前账号可见性限制，不当作失败重试。",
            "- 输出只来自本地抓包，不重放请求，不包含原始 Cookie、Authorization 或 access_token。",
            "",
            "## 输出文件",
            "",
            "- `facebook-sample-patterns.json`：机器可读汇总。",
            "- `facebook-posts.csv`：帖子摘要。",
            "- `facebook-share-users.csv`：可见转发用户。",
            "- `facebook-reactor-users.csv`：可见互动用户，含 `source_field` 和分页状态；当前样本可能为空。",
            "- `facebook-reaction-counts.csv`：表情计数。",
        ]
    )
    (args.out_dir / "facebook-sample-patterns.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"out_dir": str(args.out_dir), "records": len(records), "posts": len(posts), "shares": len(shares), "reactors": len(reactors)}, ensure_ascii=False))
    if args.require_target_match and target_verification.get("status") != "matched":
        raise SystemExit(3)


if __name__ == "__main__":
    main()
