import hashlib
import zlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from mitmproxy import ctx, http

from analyze_facebook_capture import (
    REACTION_QUERY_PREFIXES,
    decode_stream,
    extract_reactors,
    extract_share_pages,
    extract_shares,
    feedback_post_id,
    record_reaction_id,
    record_source_post_id,
    response_feedback_post_id,
    target_url_digest,
    walk,
)


OUT = Path(os.environ.get("FACEBOOK_CAPTURE_DIR", Path(__file__).resolve().parent))
JSONL = OUT / "safe-facebook-http-capture.jsonl"
LIVE_PROGRESS = OUT / "facebook-live-progress.json"
MAX_FLOWS = int(os.environ.get("FACEBOOK_CAPTURE_MAX_FLOWS", "0"))
MAX_BODY_BYTES = int(os.environ.get("FACEBOOK_CAPTURE_MAX_BODY_BYTES", str(6 * 1024 * 1024)))
FLOW_COUNT = 0
REACTION_PREFIX = REACTION_QUERY_PREFIXES
SHARE_PREFIX = "ResharesOfContentFeedQuery"
NULL_REACTION_KEY = "__ALL__"
REACTION_RATE_LIMIT_CODE = 1675004
TEMPORARY_FEATURE_BLOCK_GRAPHQL_CODE = 3252001
TEMPORARY_FEATURE_BLOCK_API_CODE = 368
SAFE_REQUEST_HEADER_PRESENCE = (
    "content-type",
    "x-asbd-id",
    "x-fb-connection-type",
    "x-fb-friendly-name",
    "x-fb-http-engine",
    "x-fb-lsd",
)


def _existing_jsonl_line_count():
    if not JSONL.exists():
        return 0
    try:
        with JSONL.open(encoding="utf-8") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


RECORD_LINE_COUNT = _existing_jsonl_line_count()
LIVE_STATE = {}
REACTION_CURSOR_LAST_END_HASH = {}
REACTION_CURSOR_SUCCESS_PAGE_COUNT = {}

ALLOWED_SUFFIXES = ("facebook.com", "fbcdn.net", "fbsbx.com", "instagram.com")
SENSITIVE_FIELD_NAMES = (
    "access_token",
    "authorization",
    "cookie",
    "set-cookie",
    "fb_dtsg",
    "session_id",
    "c_user",
    "xs",
    "fr",
    "datr",
    "token",
)
SENSITIVE_FIELD_PATTERN = "|".join(re.escape(name) for name in SENSITIVE_FIELD_NAMES)
# Require a literal query delimiter and leave JSON's escaped quotes intact.
# Reel responses embed DASH XML inside strings; consuming the backslash before
# an XML attribute's closing quote makes the entire GraphQL document unreadable.
URL_QUERY = re.compile(r'(?i)(https?://(?:\\(?!")|[^"\\\s?])+)\?(?:\\(?!")|[^"\\\s])+')
ESCAPED_URL_QUERY = re.compile(r'(?i)(https?:\\/\\/(?:\\(?!")|[^"\\\s?])+)\?(?:\\(?!")|[^"\\\s])+')
JSON_SENSITIVE_VALUE = re.compile(
    rf'(?i)(?P<prefix>"(?:{SENSITIVE_FIELD_PATTERN})"\s*:\s*)'
    r'(?P<value>"(?:\\.|[^"\\])*"|[^,}\]\s]+)'
)
FORM_SENSITIVE_VALUE = re.compile(
    rf'(?i)(?<![\w-])(?P<name>{SENSITIVE_FIELD_PATTERN})(?P<separator>=|%3[dD])[^&\s,;]+'
)
COLON_SENSITIVE_VALUE = re.compile(
    rf'(?i)(?<![\w-])(?P<name>{SENSITIVE_FIELD_PATTERN})\s*:\s*(?!["\'])[^\s,;}}\]]+'
)


def _new_family_state():
    return {
        "sources": {},
        "untracked": {"query_records": 0, "first_line": 0, "latest_line": 0},
    }


def _reset_live_progress_state():
    global LIVE_STATE, REACTION_CURSOR_LAST_END_HASH, REACTION_CURSOR_SUCCESS_PAGE_COUNT
    LIVE_STATE = {
        "reaction": _new_family_state(),
        "share": _new_family_state(),
        "session": {
            "temporary_feature_block_count": 0,
            "latest_temporary_feature_block_line": 0,
            "latest_temporary_feature_block_graphql_code": None,
            "latest_temporary_feature_block_api_code": None,
            "latest_temporary_feature_block_friendly_name": "",
        },
    }
    REACTION_CURSOR_LAST_END_HASH = {}
    REACTION_CURSOR_SUCCESS_PAGE_COUNT = {}


_reset_live_progress_state()


def _reaction_key(value):
    return str(value) if value not in (None, "") else NULL_REACTION_KEY


def _source_state(family, source_post_id, line_number):
    family_state = LIVE_STATE[family]
    key = str(source_post_id)
    state = family_state["sources"].get(key)
    if state is None:
        state = {
            "family": family,
            "source_post_id": key,
            "query_records": 0,
            "first_line": int(line_number),
            "latest_line": int(line_number),
            "page_count": 0,
            "terminal_page_seen": False,
            "open_page_seen": False,
            "_user_keys": set(),
        }
        if family == "reaction":
            state["latest_group_key"] = None
            state["_groups"] = {}
            state["rate_limit_count"] = 0
            state["latest_rate_limited"] = False
            state["latest_graphql_error_code"] = None
        family_state["sources"][key] = state
    return state


def _mark_untracked(family, line_number):
    value = LIVE_STATE[family]["untracked"]
    value["query_records"] += 1
    if not value["first_line"]:
        value["first_line"] = int(line_number)
    value["latest_line"] = int(line_number)


def _row_user_key(row, id_field, name_field):
    value = row.get(id_field)
    if value:
        return str(value)
    name = row.get(name_field)
    return f"name:{name}" if name else None


def _update_reaction_progress(line_number, record, source_post_id):
    state = _source_state("reaction", source_post_id, line_number)
    state["query_records"] += 1
    state["latest_line"] = int(line_number)
    reaction_key = _reaction_key(record_reaction_id(record))
    state["latest_group_key"] = reaction_key
    rate_limited = bool(record.get("response_rate_limited"))
    state["latest_rate_limited"] = rate_limited
    state["latest_graphql_error_code"] = record.get("response_graphql_error_code")
    if rate_limited:
        state["rate_limit_count"] += 1
    reactors, pages = extract_reactors([(line_number, record)], redact=False)
    for row in reactors:
        raw_key = _row_user_key(row, "user_id", "user_name")
        if raw_key:
            state["_user_keys"].add(raw_key)
    state["page_count"] += len(pages)
    state["terminal_page_seen"] = state["terminal_page_seen"] or any(page.get("has_next_page") is False for page in pages)
    state["open_page_seen"] = state["open_page_seen"] or any(page.get("has_next_page") is True for page in pages)

    groups = state["_groups"]
    group = groups.get(reaction_key)
    if group is None:
        group = {
            "reaction_key": reaction_key,
            "query_records": 0,
            "first_line": int(line_number),
            "latest_line": int(line_number),
            "page_count": 0,
            "terminal_page_seen": False,
            "open_page_seen": False,
            "rate_limit_count": 0,
            "latest_rate_limited": False,
            "latest_graphql_error_code": None,
            "_user_keys": set(),
        }
        groups[reaction_key] = group
    group["query_records"] += 1
    group["latest_line"] = int(line_number)
    group["latest_rate_limited"] = rate_limited
    group["latest_graphql_error_code"] = record.get("response_graphql_error_code")
    if rate_limited:
        group["rate_limit_count"] += 1
    group["page_count"] += len(pages)
    group["terminal_page_seen"] = group["terminal_page_seen"] or any(page.get("has_next_page") is False for page in pages)
    group["open_page_seen"] = group["open_page_seen"] or any(page.get("has_next_page") is True for page in pages)
    for row in reactors:
        raw_key = _row_user_key(row, "user_id", "user_name")
        if raw_key:
            group["_user_keys"].add(raw_key)


def _update_share_progress(line_number, record, source_post_id):
    # Share live counters do not carry rate errors; force exact JSONL fallback.
    if record.get("response_rate_limited") or record.get("response_graphql_error_code") == 1675004:
        _mark_untracked("share", line_number)
    state = _source_state("share", source_post_id, line_number)
    state["query_records"] += 1
    state["latest_line"] = int(line_number)
    shares = extract_shares([(line_number, record)], redact=False)
    pages = extract_share_pages([(line_number, record)], redact=False)
    for row in shares:
        raw_key = row.get("share_story_id") or row.get("legacy_api_story_id") or _row_user_key(row, "actor_id", "actor_name")
        if raw_key:
            state["_user_keys"].add(str(raw_key))
    state["page_count"] += len(pages)
    state["terminal_page_seen"] = state["terminal_page_seen"] or any(page.get("has_next_page") is False for page in pages)
    state["open_page_seen"] = state["open_page_seen"] or any(page.get("has_next_page") is True for page in pages)


def _update_session_progress(line_number, record):
    if not bool(record.get("response_temporary_feature_block")):
        return False
    state = LIVE_STATE["session"]
    state["temporary_feature_block_count"] += 1
    state["latest_temporary_feature_block_line"] = int(line_number)
    state["latest_temporary_feature_block_graphql_code"] = record.get("response_graphql_error_code")
    state["latest_temporary_feature_block_api_code"] = record.get("response_api_error_code")
    state["latest_temporary_feature_block_friendly_name"] = str(record.get("friendly_name") or "")
    return True


def _update_live_progress(line_number, record):
    session_updated = _update_session_progress(line_number, record)
    friendly = str(record.get("friendly_name") or "")
    if friendly.startswith(REACTION_PREFIX):
        family = "reaction"
    elif friendly.startswith(SHARE_PREFIX):
        family = "share"
    else:
        return session_updated
    source_post_id = record_source_post_id(record)
    if not source_post_id:
        _mark_untracked(family, line_number)
    elif family == "reaction":
        _update_reaction_progress(line_number, record, str(source_post_id))
    else:
        _update_share_progress(line_number, record, str(source_post_id))
    return True


def _public_group_snapshot(group):
    return {
        "reaction_key": group["reaction_key"],
        "query_records": group["query_records"],
        "user_count": len(group["_user_keys"]),
        "first_line": group["first_line"],
        "latest_line": group["latest_line"],
        "page_count": group["page_count"],
        "terminal_page_seen": bool(group["terminal_page_seen"]),
        "open_page_seen": bool(group["open_page_seen"]),
        "rate_limit_count": int(group.get("rate_limit_count", 0)),
        "latest_rate_limited": bool(group.get("latest_rate_limited", False)),
        "latest_graphql_error_code": group.get("latest_graphql_error_code"),
    }


def _public_source_snapshot(state):
    result = {
        "family": state["family"],
        "source_post_id": state["source_post_id"],
        "query_records": state["query_records"],
        "user_count": len(state["_user_keys"]),
        "first_line": state["first_line"],
        "latest_line": state["latest_line"],
        "page_count": state["page_count"],
        "terminal_page_seen": bool(state["terminal_page_seen"]),
        "open_page_seen": bool(state["open_page_seen"]),
    }
    if state["family"] == "reaction":
        result["latest_group_key"] = state["latest_group_key"]
        result["rate_limit_count"] = int(state.get("rate_limit_count", 0))
        result["latest_rate_limited"] = bool(state.get("latest_rate_limited", False))
        result["latest_graphql_error_code"] = state.get("latest_graphql_error_code")
        result["groups"] = {
            key: _public_group_snapshot(group)
            for key, group in state["_groups"].items()
        }
    return result


def _live_progress_snapshot():
    return {
        "version": 2,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "record_line_count": int(RECORD_LINE_COUNT),
        "families": {
            family: {
                "sources": {
                    source_id: _public_source_snapshot(source)
                    for source_id, source in family_state["sources"].items()
                },
                "untracked": dict(family_state["untracked"]),
            }
            for family, family_state in ((name, LIVE_STATE[name]) for name in ("reaction", "share"))
        },
        "session": dict(LIVE_STATE["session"]),
    }


def _write_live_progress_snapshot(path=None):
    target = Path(path) if path is not None else LIVE_PROGRESS
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    temp.write_text(json.dumps(_live_progress_snapshot(), ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(temp, target)


def _is_allowed(host):
    host = (host or "").lower().rstrip(".")
    return host.endswith(ALLOWED_SUFFIXES)


def _request_query_keys(url):
    try:
        return sorted({key for key, _ in parse_qsl(urlsplit(url).query, keep_blank_values=True)})
    except Exception:
        return []


def _find_variable(value, names):
    if isinstance(value, dict):
        for name in names:
            if value.get(name) not in (None, ""):
                return value[name]
        for child in value.values():
            found = _find_variable(child, names)
            if found not in (None, ""):
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_variable(child, names)
            if found not in (None, ""):
                return found
    return None


def _decode_feedback_post_id(value):
    if not value:
        return None
    value = str(value).strip()
    if re.fullmatch(r"[0-9]+", value):
        return value
    return feedback_post_id(value)


def _public_request_context_from_values(values):
    values = values or {}
    variables = {}
    raw_variables = values.get("variables")
    if raw_variables:
        try:
            variables = json.loads(raw_variables)
        except json.JSONDecodeError:
            variables = {}
    raw_feedback_id = _find_variable(variables, ("feedback_id", "feedbackId", "feedbackID"))
    if raw_feedback_id in (None, ""):
        raw_feedback_id = values.get("feedback_id")
    reaction_id = _find_variable(variables, ("reaction_id", "reactionId", "reactionID"))
    if reaction_id in (None, ""):
        reaction_id = values.get("reaction_id")
    original_content_id = _find_variable(
        variables,
        ("original_content_id", "originalContentId", "originalContentID"),
    )
    if original_content_id in (None, ""):
        original_content_id = values.get("original_content_id")
    context = {}
    post_id = _decode_feedback_post_id(raw_feedback_id)
    if post_id:
        context["request_feedback_post_id"] = post_id
    if reaction_id not in (None, "") and len(str(reaction_id)) <= 128:
        context["request_reaction_id"] = str(reaction_id)
    if original_content_id not in (None, "") and len(str(original_content_id)) <= 256:
        context["request_original_content_id"] = str(original_content_id)
    return context


def _public_navigation_context_from_values(values):
    variables = _variable_values(values)
    context = {}
    navigation_url = _find_variable(variables, ("url",))
    if navigation_url not in (None, ""):
        value = str(navigation_url).strip()
        try:
            parsed = urlsplit(value)
            host = (parsed.hostname or "").lower()
        except ValueError:
            host = ""
        if len(value) <= 2048 and host in {"facebook.com", "www.facebook.com", "m.facebook.com"}:
            context["request_navigation_url_hash"] = target_url_digest(value)
    seed_video_id = _find_variable(variables, ("seed_video_id", "video_id_to_filter"))
    if seed_video_id not in (None, ""):
        seed = str(seed_video_id).strip()
        if re.fullmatch(r"\d{1,32}", seed):
            context["request_seed_video_id"] = seed
    return context


def _public_request_context_from_body(body):
    parsed = dict(parse_qsl(body or "", keep_blank_values=True))
    return _public_request_context_from_values(parsed)


def _request_cursor_from_values(values):
    values = values or {}
    variables = {}
    raw_variables = values.get("variables")
    if raw_variables:
        try:
            variables = json.loads(raw_variables)
        except json.JSONDecodeError:
            variables = {}
    cursor = _find_variable(variables, ("after", "cursor", "after_cursor", "afterCursor"))
    if cursor in (None, ""):
        for key in ("after", "cursor", "after_cursor", "afterCursor"):
            if values.get(key) not in (None, ""):
                cursor = values.get(key)
                break
    return None if cursor in (None, "") else str(cursor)


def _variable_values(values):
    values = values or {}
    raw_variables = values.get("variables")
    if not raw_variables:
        return {}
    try:
        parsed = json.loads(raw_variables)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, (dict, list)) else {}


def _is_sensitive_variable_name(name):
    normalized = str(name or "").strip().lower().replace("-", "_")
    if normalized in SENSITIVE_FIELD_NAMES:
        return True
    return any(marker in normalized for marker in ("token", "cookie", "authorization", "session", "fb_dtsg"))


def _safe_variable_shape(value, prefix=""):
    """Return key paths/container markers only; never persist GraphQL variable values."""
    shape = set()
    if isinstance(value, dict):
        for key in sorted(value):
            if _is_sensitive_variable_name(key):
                continue
            path = f"{prefix}.{key}" if prefix else str(key)
            shape.add(path)
            shape.update(_safe_variable_shape(value[key], path))
    elif isinstance(value, list):
        marker = f"{prefix}[]" if prefix else "[]"
        shape.add(marker)
        for child in value:
            shape.update(_safe_variable_shape(child, marker))
    return shape


def _safe_request_structure(values, headers=None, friendly_name=None):
    """Build a diagnostic request fingerprint from names/shape/presence only."""
    values = dict(values or {})
    safe_form_keys = sorted(
        str(key) for key in values
        if not _is_sensitive_variable_name(key)
    )
    variables = _variable_values(values)
    header_presence = {
        name: bool((headers or {}).get(name))
        for name in SAFE_REQUEST_HEADER_PRESENCE
    }
    public = {
        "request_form_keys": safe_form_keys,
        "request_variable_shape": sorted(_safe_variable_shape(variables)),
        "request_header_presence": header_presence,
        "request_structure_friendly_name": str(friendly_name or ""),
    }
    canonical = json.dumps(public, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    public["request_context_fingerprint"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return public


def _non_sensitive_scalar_hashes(value, parent_name=None):
    hashes = set()
    if _is_sensitive_variable_name(parent_name):
        return hashes
    if isinstance(value, dict):
        for key, child in value.items():
            if not _is_sensitive_variable_name(key):
                hashes.update(_non_sensitive_scalar_hashes(child, key))
    elif isinstance(value, list):
        for child in value:
            hashes.update(_non_sensitive_scalar_hashes(child, parent_name))
    elif value not in (None, "") and not isinstance(value, (dict, list)):
        hashes.add(_cursor_digest(value))
    return hashes


def _request_scalar_hashes_from_values(values):
    return _non_sensitive_scalar_hashes(_variable_values(values))


def _request_scalar_hashes_from_body(body):
    return _request_scalar_hashes_from_values(dict(parse_qsl(body or "", keep_blank_values=True)))


def _request_cursor_from_body(body):
    return _request_cursor_from_values(dict(parse_qsl(body or "", keep_blank_values=True)))


def _cursor_digest(value):
    if value in (None, ""):
        return None
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _reaction_cursor_key(context):
    post_id = context.get("request_feedback_post_id") if isinstance(context, dict) else None
    if not post_id:
        return None
    reaction_id = context.get("request_reaction_id") if isinstance(context, dict) else None
    return (str(post_id), _reaction_key(reaction_id))


def _reaction_cursor_request_probe(friendly, context, request_body="", form_values=None):
    if not str(friendly or "").startswith(REACTION_PREFIX):
        return {}
    cursor = _request_cursor_from_body(request_body)
    if cursor is None and form_values:
        cursor = _request_cursor_from_values(form_values)
    key = _reaction_cursor_key(context)
    previous_hash = REACTION_CURSOR_LAST_END_HASH.get(key) if key else None
    cursor_hash = _cursor_digest(cursor)
    generic_match = False
    if previous_hash is not None and cursor_hash is None:
        scalar_hashes = _request_scalar_hashes_from_body(request_body)
        if form_values:
            scalar_hashes.update(_request_scalar_hashes_from_values(form_values))
        generic_match = previous_hash in scalar_hashes
    return {
        "request_cursor_present": cursor_hash is not None or generic_match,
        "previous_end_cursor_available": previous_hash is not None,
        "request_cursor_matches_previous_end": (
            None
            if previous_hash is None
            else (cursor_hash == previous_hash if cursor_hash is not None else generic_match)
        ),
    }


def _reaction_page_info(response_body):
    for document in decode_stream(response_body or ""):
        for value in walk(document):
            if not isinstance(value, dict):
                continue
            for field in ("reactors", "important_reactors", "friend_reactors"):
                connection = value.get(field)
                if not isinstance(connection, dict):
                    continue
                page_info = connection.get("page_info")
                if isinstance(page_info, dict):
                    return page_info
    return {}


def _graphql_error_probe(response_body):
    first_code = None
    first_api_code = None
    rate_limited = False
    temporary_feature_block = False
    for document in decode_stream(response_body or ""):
        if not isinstance(document, dict):
            continue
        errors = document.get("errors")
        if not isinstance(errors, list):
            continue
        for error in errors:
            if not isinstance(error, dict):
                continue
            code = error.get("code")
            api_code = error.get("api_error_code")
            message = str(error.get("message") or "")
            if first_code is None and code is not None:
                first_code = code
            if first_api_code is None and api_code is not None:
                first_api_code = api_code
            if code == REACTION_RATE_LIMIT_CODE or "rate limit exceeded" in message.lower():
                rate_limited = True
                if code is None:
                    first_code = REACTION_RATE_LIMIT_CODE
            if code == TEMPORARY_FEATURE_BLOCK_GRAPHQL_CODE or api_code == TEMPORARY_FEATURE_BLOCK_API_CODE:
                temporary_feature_block = True
    return {
        "response_rate_limited": rate_limited,
        "response_graphql_error_code": first_code,
        "response_api_error_code": first_api_code,
        "response_temporary_feature_block": temporary_feature_block,
    }


def _reaction_graphql_error_probe(response_body):
    return _graphql_error_probe(response_body)


def _reaction_edge_count(response_body):
    for document in decode_stream(response_body or ""):
        for value in walk(document):
            if not isinstance(value, dict):
                continue
            for field in ("reactors", "important_reactors", "friend_reactors"):
                connection = value.get(field)
                if isinstance(connection, dict) and isinstance(connection.get("edges"), list):
                    return len(connection["edges"])
    return 0


def _reaction_cursor_response_probe(record, response_body):
    friendly = str(record.get("friendly_name") or "") if isinstance(record, dict) else ""
    if not friendly.startswith(REACTION_PREFIX):
        return {}
    error_probe = _reaction_graphql_error_probe(response_body)
    key = _reaction_cursor_key(record)
    successful_pages = REACTION_CURSOR_SUCCESS_PAGE_COUNT.get(key, 0) if key else 0
    if error_probe["response_rate_limited"]:
        return {
            **error_probe,
            "response_reactor_edge_count": 0,
            "response_reactor_page_index": successful_pages + 1,
            "response_page_advanced": False,
            "response_cursor_checkpoint_preserved": bool(key and key in REACTION_CURSOR_LAST_END_HASH),
            "response_end_cursor_present": False,
            "response_has_next_page": None,
        }
    page_info = _reaction_page_info(response_body)
    if not page_info:
        return {
            **error_probe,
            "response_reactor_edge_count": _reaction_edge_count(response_body),
            "response_reactor_page_index": successful_pages + 1,
            "response_page_advanced": False,
            "response_cursor_checkpoint_preserved": bool(key and key in REACTION_CURSOR_LAST_END_HASH),
            "response_end_cursor_present": False,
            "response_has_next_page": None,
        }
    end_cursor = page_info.get("end_cursor")
    end_hash = _cursor_digest(end_cursor)
    if key and end_hash is not None:
        REACTION_CURSOR_LAST_END_HASH[key] = end_hash
        successful_pages += 1
        REACTION_CURSOR_SUCCESS_PAGE_COUNT[key] = successful_pages
    return {
        **error_probe,
        "response_reactor_edge_count": _reaction_edge_count(response_body),
        "response_reactor_page_index": successful_pages if end_hash is not None else successful_pages + 1,
        "response_page_advanced": end_hash is not None,
        "response_cursor_checkpoint_preserved": False,
        "response_end_cursor_present": end_hash is not None,
        "response_has_next_page": page_info.get("has_next_page"),
    }


def _friendly_name(headers, body):
    value = headers.get("x-fb-friendly-name")
    if value:
        return value
    match = re.search(r"(?:^|&)fb_api_req_friendly_name=([^&]+)", body or "")
    return match.group(1) if match else None


def _redact_text(text):
    text = text or ""
    text = URL_QUERY.sub(r"\1", text)
    text = ESCAPED_URL_QUERY.sub(r"\1", text)
    # Match complete field names so ordinary names such as is_groups_ifr remain intact.
    text = JSON_SENSITIVE_VALUE.sub(
        lambda match: f'{match.group("prefix")}"[REDACTED]"',
        text,
    )
    text = FORM_SENSITIVE_VALUE.sub(
        lambda match: f'{match.group("name")}{match.group("separator")}[REDACTED]',
        text,
    )
    text = COLON_SENSITIVE_VALUE.sub(
        lambda match: f'{match.group("name")}: [REDACTED]',
        text,
    )
    return text


def _body_text(message):
    if message is None:
        return None
    try:
        raw = message.raw_content or b""
        if len(raw) > MAX_BODY_BYTES:
            return None
        return _redact_text(message.get_text(strict=False))
    except Exception:
        return None


def _bounded_request_decompress(raw, wbits):
    try:
        decoder = zlib.decompressobj(wbits)
        decoded = decoder.decompress(raw, MAX_BODY_BYTES + 1)
        if len(decoded) > MAX_BODY_BYTES or not decoder.eof:
            return None
        tail = decoder.flush()
        if len(decoded) + len(tail) > MAX_BODY_BYTES:
            return None
        return decoded + tail
    except Exception:
        return None


def _request_body_text(message):
    if message is None:
        return ""
    try:
        raw = message.raw_content or b""
    except Exception:
        return ""
    if len(raw) > MAX_BODY_BYTES:
        return ""

    try:
        encoding = str(message.headers.get("content-encoding") or "").strip().lower()
    except Exception:
        encoding = ""

    decoded = raw
    if raw.startswith(b"\x1f\x8b") or "gzip" in encoding:
        decoded = _bounded_request_decompress(raw, 16 + zlib.MAX_WBITS)
    elif "deflate" in encoding:
        decoded = _bounded_request_decompress(raw, zlib.MAX_WBITS)
        if decoded is None:
            decoded = _bounded_request_decompress(raw, -zlib.MAX_WBITS)
    elif encoding not in ("", "identity"):
        return ""

    if decoded is None or len(decoded) > MAX_BODY_BYTES:
        return ""
    return decoded.decode("utf-8", errors="replace")


def request(flow: http.HTTPFlow):
    global FLOW_COUNT
    host = flow.request.pretty_host
    if not _is_allowed(host):
        return
    FLOW_COUNT += 1
    request_body = _request_body_text(flow.request)
    body = _redact_text(request_body)
    friendly = _friendly_name(flow.request.headers, body)
    form_values = dict(parse_qsl(request_body or "", keep_blank_values=True))
    flow.metadata["safe_capture"] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "id": flow.id,
        "host": host,
        "method": flow.request.method,
        "path": urlsplit(flow.request.pretty_url).path or "/",
        "url_query_keys": _request_query_keys(flow.request.pretty_url),
        "friendly_name": friendly,
        "request_body_bytes": len(flow.request.raw_content or b""),
    }
    flow.metadata["safe_capture"].update(
        _safe_request_structure(form_values, flow.request.headers, friendly)
    )
    context = _public_request_context_from_body(request_body)
    if not context and not form_values:
        try:
            form_values = flow.request.urlencoded_form
            context = _public_request_context_from_values(form_values)
            flow.metadata["safe_capture"].update(
                _safe_request_structure(form_values, flow.request.headers, friendly)
            )
        except Exception:
            context = {}
    flow.metadata["safe_capture"].update(context)
    flow.metadata["safe_capture"].update(_public_navigation_context_from_values(form_values))
    flow.metadata["safe_capture"].update(
        _reaction_cursor_request_probe(friendly, context, request_body, form_values)
    )
    if MAX_FLOWS > 0 and FLOW_COUNT >= MAX_FLOWS:
        flow.metadata["safe_capture_stop_after_response"] = True


def response(flow: http.HTTPFlow):
    global RECORD_LINE_COUNT
    record = flow.metadata.get("safe_capture")
    if record is None:
        return
    response_body = _body_text(flow.response)
    record.update(
        {
            "status_code": flow.response.status_code if flow.response else None,
            "response_content_type": flow.response.headers.get("content-type") if flow.response else None,
            "response_body_bytes": len(flow.response.raw_content or b"") if flow.response else 0,
            "response_body": response_body,
        }
    )
    response_source = response_feedback_post_id(record)
    if response_source:
        record["response_feedback_post_id"] = response_source
    record.update(_graphql_error_probe(response_body))
    record.update(_reaction_cursor_response_probe(record, response_body))
    OUT.mkdir(parents=True, exist_ok=True)
    with JSONL.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    RECORD_LINE_COUNT += 1
    if _update_live_progress(RECORD_LINE_COUNT, record):
        _write_live_progress_snapshot()
    if flow.metadata.get("safe_capture_stop_after_response"):
        ctx.master.shutdown()
