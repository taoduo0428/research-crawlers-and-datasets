"""Conservative UI readiness proof for the Facebook target-open wait.

The target-open PowerShell flow may use this helper as an acceleration probe.
It is deliberately fail-closed: a false result only keeps the existing bounded
wait, while a true result requires a fresh network target verification and a
single post whose author and distinctive text are visible in the current UI.
"""

from __future__ import annotations

import argparse
import csv
import html
import io
import json
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

from analyze_facebook_capture import (
    _target_url_components,
    _url_component_match,
    extract_posts,
    iter_records,
    verify_target_capture,
)


MAX_CAPTURE_BYTES = 16 * 1024 * 1024
MAX_CAPTURE_RECORDS = 100
MAX_TEXT_BYTES = 4 * 1024 * 1024
MIN_MESSAGE_NONSPACE = 40
MIN_MESSAGE_ALNUM = 8

_FACEBOOK_PACKAGE = "com.facebook.katana"
_CLOSE_RE = re.compile(r"^(?:close|dismiss|关闭|關閉)$", re.IGNORECASE)
_COMMENT_RE = re.compile(
    r"(?:comment|reply|write\s+a?\s*comment|添加评论|发表公开评论|写评论|留言|评论)",
    re.IGNORECASE,
)
_ACTION_RE = re.compile(
    r"(?:like|comment|reply|share|reaction|react|赞|讚|评论|評論|回复|回覆|分享|心情|情绪|情緒)",
    re.IGNORECASE,
)
_SEARCH_PAGE_RE = re.compile(
    r"(?:search\s+results?|search\s+posts?|搜索结果|搜尋結果|搜索帖子|在\s*facebook\s*上搜索|"
    r"在\s*facebook\s*上搜尋|请输入搜索|請輸入搜尋)",
    re.IGNORECASE,
)
_BLOCKED_PAGE_RE = re.compile(
    r"(?:content\s+(?:isn't|is not)\s+available|page\s+(?:isn't|is not)\s+available|"
    r"this\s+content\s+is\s+not\s+available|you\s+can't\s+view|"
    r"内容不可用|内容目前不可用|此内容目前不可用|帖子不可用|无法查看此内容|"
    r"应用无响应|应用程序无响应|isn't responding|not responding|checkpoint)",
    re.IGNORECASE,
)
_OFFLINE_RE = re.compile(
    r"(?:\boffline\b|no\s+internet|network\s+(?:unavailable|error)|"
    r"无网络|没有网络|网络不可用|无法连接网络|重新连接)",
    re.IGNORECASE,
)
_LOADING_RE = re.compile(
    r"(?:\bloading\b|加载中|正在加载|载入中|请稍候|please\s+wait)",
    re.IGNORECASE,
)
_LOGIN_RE = re.compile(
    r"(?:log\s+in\s+to\s+facebook|登录\s*facebook|登入\s*facebook|security\s+check)",
    re.IGNORECASE,
)


class _UiNode:
    """Small parsed view of a UI node, retaining ancestry for card grouping."""

    __slots__ = (
        "element",
        "parent",
        "depth",
        "enabled",
        "visible",
        "texts",
        "package",
        "class_name",
        "resource_id",
        "clickable",
        "bounds_valid",
    )

    def __init__(
        self,
        element: ET.Element,
        parent: ET.Element | None,
        depth: int,
        inherited_visible: bool = True,
        inherited_package: str = "",
    ) -> None:
        self.element = element
        self.parent = parent
        self.depth = depth
        own_enabled = element.attrib.get("enabled")
        if own_enabled is None:
            own_enabled = "true"
        # Android's enabled state belongs to this View, not its descendants.
        # Real Facebook dumps contain disabled layout containers around enabled
        # RecyclerView children. Visibility, unlike enabled, is inherited.
        self.enabled = own_enabled.strip().lower() == "true"
        visible_value = element.attrib.get("visible-to-user")
        if visible_value is None:
            visible_value = element.attrib.get("visible")
        own_visible = visible_value is None or visible_value.strip().lower() == "true"
        self.visible = inherited_visible and own_visible
        self.texts = _node_texts(element)
        own_package = _norm(element.attrib.get("package", ""))
        self.package = own_package or inherited_package
        self.class_name = _norm(element.attrib.get("class", ""))
        self.resource_id = _norm(element.attrib.get("resource-id", ""))
        self.clickable = element.attrib.get("clickable", "").strip().lower() == "true"
        raw_bounds = element.attrib.get("bounds")
        if raw_bounds is None:
            self.bounds_valid: bool | None = None
        else:
            bounds = re.fullmatch(
                r"\[\s*(-?\d+)\s*,\s*(-?\d+)\s*\]\s*\[\s*(-?\d+)\s*,\s*(-?\d+)\s*\]",
                raw_bounds,
            )
            self.bounds_valid = bool(
                bounds
                and int(bounds.group(3)) > int(bounds.group(1))
                and int(bounds.group(4)) > int(bounds.group(2))
            )

    @property
    def usable(self) -> bool:
        return self.enabled and self.visible and self.bounds_valid is not False


def _norm(value: Any) -> str:
    """Normalize text exactly once for comparisons made by this module."""

    if value is None:
        return ""
    value = html.unescape(str(value))
    value = unicodedata.normalize("NFKC", value)
    return re.sub(r"\s+", " ", value).strip()


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", _norm(value))


def _node_texts(element: ET.Element) -> list[str]:
    values: list[str] = []
    for key in ("text", "content-desc"):
        value = _norm(element.attrib.get(key, ""))
        if value and value not in values:
            values.append(value)
    return values


def _parse_ui(raw_xml: str | bytes) -> tuple[ET.Element | None, list[_UiNode], str | None]:
    if isinstance(raw_xml, bytes):
        if len(raw_xml) > MAX_TEXT_BYTES:
            return None, [], "ui-too-large"
        try:
            raw_xml = raw_xml.decode("utf-8")
        except UnicodeDecodeError:
            return None, [], "ui-encoding-error"
    else:
        raw_xml = str(raw_xml or "")
        if len(raw_xml.encode("utf-8")) > MAX_TEXT_BYTES:
            return None, [], "ui-too-large"
    if not raw_xml.strip():
        return None, [], "ui-empty"
    try:
        root = ET.fromstring(raw_xml)
    except (ET.ParseError, ValueError):
        # A malformed dump is intentionally not repaired here.  The caller's
        # original wait owns the slower UI fallback and can collect diagnostics.
        return None, [], "invalid-ui-xml"

    parsed: list[_UiNode] = []
    stack: list[tuple[ET.Element, ET.Element | None, int, bool, str]] = [
        (root, None, 0, True, "")
    ]
    while stack:
        element, parent, depth, inherited_visible, inherited_package = stack.pop()
        node = _UiNode(
            element,
            parent,
            depth,
            inherited_visible,
            inherited_package,
        )
        if element.tag.rsplit("}", 1)[-1] == "node":
            parsed.append(node)
        children = list(element)
        for child in reversed(children):
            stack.append(
                (
                    child,
                    element,
                    depth + 1,
                    node.visible,
                    node.package,
                )
            )
    return root, parsed, None


def _visible_nodes(nodes: Iterable[_UiNode]) -> list[_UiNode]:
    return [node for node in nodes if node.usable]


def _visible_labels(nodes: Iterable[_UiNode]) -> list[str]:
    labels: list[str] = []
    for node in nodes:
        for text in node.texts:
            if text not in labels:
                labels.append(text)
    return labels


def _is_facebook_package(package: str) -> bool:
    value = package.lower().strip()
    return value == _FACEBOOK_PACKAGE or value.startswith(_FACEBOOK_PACKAGE + ":")


def _page_state(nodes: list[_UiNode]) -> str | None:
    if not nodes:
        return "no-visible-enabled-ui"
    facebook_nodes = [node for node in nodes if _is_facebook_package(node.package)]
    if not facebook_nodes:
        return "non-facebook-page"

    # A permission dialog, ANR sheet, or foreign application overlay can leave
    # Facebook nodes in the same dump.  Any active visible enabled foreign node
    # invalidates the proof; hidden ancestors are excluded from visible nodes.
    for node in nodes:
        if re.search(r"(?:^|/)aerr_", node.resource_id, re.IGNORECASE):
            return "foreign-overlay"
        if node.package and not _is_facebook_package(node.package):
            return "foreign-overlay"

    labels = _visible_labels(nodes)
    for node in nodes:
        if "progressbar" in node.class_name.lower():
            return "page-loading"
    for label in labels:
        if _BLOCKED_PAGE_RE.search(label) or _LOGIN_RE.search(label):
            return "page-blocked"
        if _OFFLINE_RE.search(label):
            return "page-offline"
        if _LOADING_RE.search(label):
            return "page-loading"

    if any(_SEARCH_PAGE_RE.search(label) for label in labels):
        return "search-page"
    # An EditText with a search label is a search surface.  A comment composer
    # is allowed and is distinguished by its comment wording below.
    for node in nodes:
        if "edittext" not in node.class_name.lower():
            continue
        if any(_COMMENT_RE.search(label) for label in node.texts):
            continue
        if any(re.search(r"(?:search|搜索|搜尋)", label, re.IGNORECASE) for label in node.texts):
            return "search-page"
    return None


def _detail_features(nodes: list[_UiNode]) -> bool:
    has_composer = False
    has_close = False
    for node in nodes:
        class_name = node.class_name.lower()
        if "autocomplete" in class_name or "edittext" in class_name:
            if (
                node.clickable
                and (any(_COMMENT_RE.search(label) for label in node.texts) or not node.texts)
            ):
                has_composer = True
        if node.clickable and any(_CLOSE_RE.search(label) for label in node.texts):
            has_close = True
    return has_composer and has_close


def _has_actionable_control(nodes: Iterable[_UiNode]) -> bool:
    """Require a clickable post action, rather than an arbitrary profile tap."""

    scoped_nodes = [node for node in nodes if node.usable]
    if _detail_features(scoped_nodes):
        return True
    return any(
        node.clickable and "button" in node.class_name.lower()
        and any(_ACTION_RE.search(label) for label in node.texts)
        for node in scoped_nodes
    )


def _message_prefix(message: Any) -> str | None:
    compact = _compact(message)
    if len(compact) < MIN_MESSAGE_NONSPACE:
        return None
    if sum(character.isalnum() for character in compact[:80]) < MIN_MESSAGE_ALNUM:
        return None
    return compact[:MIN_MESSAGE_NONSPACE]


def _message_matches(label: str, message: Any) -> bool:
    prefix = _message_prefix(message)
    if prefix is None:
        return False
    visible = _compact(label)
    # UI dumps can truncate at the end of a long body.  Only the beginning is
    # used, and the complete 40-character distinctive prefix remains required.
    return visible.startswith(prefix)


def _author_matches(label: str, author: Any) -> bool:
    expected = _norm(author)
    observed = _norm(label)
    if not expected or not observed:
        return False
    if observed == expected:
        return True
    escaped = re.escape(expected)
    # Facebook commonly decorates the author with original-audio/follow and
    # profile affordance labels.  Match those semantic decorations only.
    return bool(
        re.match(rf"^{escaped}(?:\s*[·•|]|\s+原创音频\b|\s+original\s+audio\b)", observed, re.IGNORECASE)
        or re.match(rf"^(?:关注|關注|follow\s+){escaped}\b", observed, re.IGNORECASE)
        or re.match(rf"^(?:查看|查看了|view)\s*{escaped}.*(?:主页|主頁|profile|page)", observed, re.IGNORECASE)
        or re.match(rf"^(?:打开\s*)?{escaped}(?:的|\s+)(?:快拍|帖子|主頁|主页|更多选项|更多選項)", observed, re.IGNORECASE)
    )


def _descendants(element: ET.Element) -> Iterable[ET.Element]:
    yield element
    for child in list(element):
        yield from _descendants(child)


def _scope_texts(element: ET.Element, node_by_element: Mapping[ET.Element, _UiNode]) -> list[str]:
    labels: list[str] = []
    for descendant in _descendants(element):
        node = node_by_element.get(descendant)
        if node is None or not node.usable:
            continue
        for label in node.texts:
            if label not in labels:
                labels.append(label)
    return labels


def _ancestors(element: ET.Element, node_by_element: Mapping[ET.Element, _UiNode]) -> list[ET.Element]:
    result: list[ET.Element] = []
    current: ET.Element | None = element
    while current is not None:
        result.append(current)
        node = node_by_element.get(current)
        current = node.parent if node else None
    return result


def _lowest_common_ancestor(
    first: ET.Element,
    second: ET.Element,
    node_by_element: Mapping[ET.Element, _UiNode],
) -> ET.Element | None:
    second_ancestors = set(_ancestors(second, node_by_element))
    for element in _ancestors(first, node_by_element):
        if element in second_ancestors:
            return element
    return None


def _candidate_rows(posts: Any) -> list[dict[str, Any]]:
    if isinstance(posts, Mapping):
        values = list(posts.values())
    elif isinstance(posts, (list, tuple)):
        values = list(posts)
    else:
        return []
    rows: list[dict[str, Any]] = []
    for value in values:
        if isinstance(value, Mapping):
            rows.append(dict(value))
    return rows


def _clean_id(value: Any) -> str:
    return str(value or "").strip()


def _expected_fields(
    verification: Mapping[str, Any], expected_post_id: str, expected_url: str
) -> tuple[str, str, str | None]:
    if _clean_id(verification.get("status")) != "matched":
        return "", "", "verification-not-matched"
    requested_id = _clean_id(expected_post_id)
    requested_url = str(expected_url or "").strip()
    actual_id = _clean_id(verification.get("expected_post_id"))
    actual_url = str(verification.get("expected_url") or "").strip()
    if requested_id and actual_id != requested_id:
        return "", "", "verification-arguments-mismatch"
    if requested_url and actual_url != requested_url:
        return "", "", "verification-arguments-mismatch"
    # Optional CLI arguments use the analyzer's recorded expectations.  This
    # keeps report mode useful while preserving strict equality when a caller
    # supplied an expectation for this invocation.
    return requested_id or actual_id, requested_url or actual_url, None


def _unique_target_rows(
    verification: Mapping[str, Any],
    posts: list[dict[str, Any]],
    expected_post_id: str,
    expected_url: str,
) -> tuple[list[dict[str, Any]], str | None]:
    components = _target_url_components(expected_url)
    numeric_tokens = (
        components["post_id"] | components["story_fbid"] | components["permalink_id"]
    )
    video_tokens = components["reel_id"] | components["video_id"]
    pfbid_tokens = components["pfbid"]
    matched_post_id = _clean_id(verification.get("matched_post_id"))

    if not expected_post_id and not expected_url:
        return [], "target-identity-missing"
    if not any(components.values()) and not expected_post_id:
        return [], "target-url-unsupported"
    if expected_post_id and numeric_tokens and expected_post_id not in numeric_tokens:
        return [], "verification-post-id-mismatch"
    if expected_post_id and expected_post_id in video_tokens:
        return [], "video-id-is-not-post-id"
    if matched_post_id and numeric_tokens and matched_post_id not in numeric_tokens:
        return [], "verification-post-id-mismatch"

    if expected_post_id:
        if matched_post_id and matched_post_id != expected_post_id:
            return [], "verification-post-id-mismatch"
        target_id = expected_post_id
        rows = [row for row in posts if _clean_id(row.get("post_id")) == target_id]
    elif matched_post_id:
        # A Reel/video URL carries a media ID, not necessarily its Story ID.
        # A strict analyzer result may supply the resolved Story ID, but the
        # media token itself must never be copied into post_id.
        if matched_post_id in video_tokens:
            return [], "video-id-is-not-post-id"
        rows = [row for row in posts if _clean_id(row.get("post_id")) == matched_post_id]
    elif video_tokens:
        # A prefetch can prove that the requested video was mentioned without
        # proving which Story is on screen.  Require the stronger resolved ID.
        return [], "target-post-not-mapped"
    elif pfbid_tokens:
        rows = [
            row
            for row in posts
            if _url_component_match(expected_url, str(row.get("url") or ""))
        ]
    else:
        rows = [
            row
            for row in posts
            if _clean_id(row.get("post_id")) in numeric_tokens
            or _url_component_match(expected_url, str(row.get("url") or ""))
        ]

    # Analyzer output can contain multiple copies of a Story.  Copies of the
    # same exact ID are one candidate; distinct IDs are ambiguous and fail.
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        row_id = _clean_id(row.get("post_id"))
        if row_id:
            by_id.setdefault(row_id, row)
    rows = list(by_id.values())
    if not rows:
        return [], "target-post-not-mapped"

    known_authors = {
        _clean_id(verification.get("resolved_author_id")),
        _clean_id(verification.get("expected_url_author_id")),
    }
    known_authors.discard("")
    if known_authors:
        rows = [
            row
            for row in rows
            if not _clean_id(row.get("author_id"))
            or _clean_id(row.get("author_id")) in known_authors
        ]
    if len(rows) != 1:
        return [], "target-post-ambiguous"
    return rows, None


def _ui_content_mapping(
    root: ET.Element,
    nodes: list[_UiNode],
    row: Mapping[str, Any],
    expected_post_id: str,
    expected_url: str,
) -> tuple[bool, str]:
    node_by_element = {node.element: node for node in nodes}
    visible = _visible_nodes(nodes)
    author_nodes = [
        node
        for node in visible
        if any(_author_matches(label, row.get("author_name")) for label in node.texts)
    ]
    body_nodes = [
        node
        for node in visible
        if any(_message_matches(label, row.get("message")) for label in node.texts)
    ]
    if not author_nodes:
        return False, "target-author-not-visible"
    if not body_nodes:
        return False, "target-content-not-visible"

    for author_node in author_nodes:
        for body_node in body_nodes:
            scope = _lowest_common_ancestor(author_node.element, body_node.element, node_by_element)
            if scope is None:
                continue
            scope_labels = _scope_texts(scope, node_by_element)
            body_prefixes = {
                _compact(label)
                for label in scope_labels
                if _message_prefix(row.get("message")) and _message_matches(label, row.get("message"))
            }
            # The nearest semantic container should contain one body.  More
            # than one distinct matching body indicates a feed/search ambiguity.
            if len(body_prefixes) > 1:
                continue
            if any(_author_matches(label, row.get("author_name")) for label in scope_labels):
                # The two static labels alone do not prove that the detail is
                # operational.  Require a semantic clickable action in this
                # post's containing card; profile/avatar taps are excluded.
                scope_nodes = [
                    node_by_element[descendant]
                    for descendant in _descendants(scope)
                    if descendant in node_by_element
                ]
                # Detail composers and their close buttons are often siblings
                # of the content list, outside the author/body container.
                # A profile link or the body itself is not a post action.
                controls = [node for node in scope_nodes if node not in author_nodes and node not in body_nodes]
                if not (_detail_features(visible) or _has_actionable_control(controls)):
                    continue
                return True, "target-author-and-content"
    return False, "target-ui-mismatch"


def _ui_row_mapping(
    root: ET.Element,
    nodes: list[_UiNode],
    row: Mapping[str, Any],
    expected_post_id: str,
    expected_url: str,
) -> tuple[bool, str]:
    """Apply the same author/body test to one row for ambiguity detection."""

    return _ui_content_mapping(root, nodes, row, expected_post_id, expected_url)


def evaluate_target_ui(
    raw_xml: str | bytes,
    verification: Mapping[str, Any] | None,
    posts: Any,
    *,
    expected_post_id: str = "",
    expected_url: str = "",
    require_detail: bool = False,
) -> dict[str, Any]:
    """Return whether the current UI and fresh capture prove one target post.

    The function has no side effects and performs no navigation.  ``False``
    means the caller should retain its ordinary bounded wait.
    """

    if not isinstance(verification, Mapping):
        return {"ready": False, "reason": "verification-invalid"}
    effective_id, effective_url, error = _expected_fields(
        verification, _clean_id(expected_post_id), str(expected_url or "").strip()
    )
    if error:
        return {"ready": False, "reason": error}

    root, parsed_nodes, parse_error = _parse_ui(raw_xml)
    if parse_error:
        return {"ready": False, "reason": parse_error}
    visible = _visible_nodes(parsed_nodes)
    page_error = _page_state(visible)
    if page_error:
        return {"ready": False, "reason": page_error}
    if require_detail and not _detail_features(visible):
        return {"ready": False, "reason": "detail-not-ready"}
    if root is None:
        return {"ready": False, "reason": "invalid-ui-xml"}

    rows, row_error = _unique_target_rows(
        verification, _candidate_rows(posts), effective_id, effective_url
    )
    if row_error:
        return {"ready": False, "reason": row_error}
    row = rows[0]
    result, reason = _ui_content_mapping(root, parsed_nodes, row, effective_id, effective_url)
    if not result:
        return {"ready": False, "reason": reason}
    # A current feed can expose more than one fully rendered Story.  Even when
    # the network mapping selected one row, accepting a UI with two complete
    # candidate cards would make an early exit ambiguous.  Short labels and
    # unrelated media previews do not count because they fail the same
    # distinctive 40-character prefix rule.
    visible_row_ids: set[str] = set()
    for candidate in _candidate_rows(posts):
        candidate_id = _clean_id(candidate.get("post_id"))
        if not candidate_id:
            continue
        mapped, _ = _ui_row_mapping(
            root, parsed_nodes, candidate, effective_id, effective_url
        )
        if mapped:
            visible_row_ids.add(candidate_id)
    if len(visible_row_ids) > 1:
        return {"ready": False, "reason": "target-post-ambiguous"}
    output: dict[str, Any] = {"ready": True, "reason": reason}
    matched_post_id = _clean_id(row.get("post_id")) or _clean_id(verification.get("matched_post_id"))
    if matched_post_id:
        output["matched_post_id"] = matched_post_id
    return output


def _json_result(ready: Mapping[str, Any]) -> str:
    return json.dumps(dict(ready), ensure_ascii=False, separators=(",", ":"))


def _read_bounded_bytes(path: Path, limit: int = MAX_TEXT_BYTES) -> bytes | None:
    try:
        if path.stat().st_size > limit:
            return None
        with path.open("rb") as handle:
            data = handle.read(limit + 1)
    except (OSError, ValueError):
        return None
    if len(data) > limit:
        return None
    return data


def _load_report_posts(report_path: Path, posts_path: Path) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str | None]:
    report_bytes = _read_bounded_bytes(report_path)
    posts_bytes = _read_bounded_bytes(posts_path)
    if report_bytes is None or posts_bytes is None:
        return None, [], "input-too-large-or-unreadable"
    try:
        report = json.loads(report_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, [], "report-invalid"
    if not isinstance(report, Mapping) or not isinstance(report.get("target_verification"), Mapping):
        return None, [], "report-target-verification-missing"
    try:
        rows = list(csv.DictReader(io.StringIO(posts_bytes.decode("utf-8-sig"))))
    except (UnicodeDecodeError, csv.Error):
        return None, [], "posts-invalid"
    if any(not isinstance(row, dict) for row in rows):
        return None, [], "posts-invalid"
    return dict(report["target_verification"]), rows, None


def _decode_complete_stream(body: str) -> list[Any] | None:
    """Decode all concatenated JSON documents, rejecting a partial tail."""

    if not body:
        return []
    decoder = json.JSONDecoder()
    index = 0
    parsed: list[Any] = []
    while index < len(body):
        while index < len(body) and body[index].isspace():
            index += 1
        if index >= len(body):
            break
        try:
            value, end = decoder.raw_decode(body, index)
        except json.JSONDecodeError:
            return None
        parsed.append(value)
        index = end
    if not parsed and body.strip():
        return None
    return parsed


def _load_capture_impl(capture_path: Path, min_line: int, expected_post_id: str, expected_url: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str | None]:
    try:
        if capture_path.stat().st_size > MAX_CAPTURE_BYTES:
            return None, [], "capture-too-large"
    except (OSError, ValueError):
        return None, [], "capture-unreadable"
    try:
        records_with_lines, parse_errors = iter_records(
            capture_path, MAX_CAPTURE_RECORDS + 1, max(1, int(min_line or 1))
        )
    except (OSError, UnicodeError, ValueError):
        return None, [], "capture-unreadable"
    try:
        if capture_path.stat().st_size > MAX_CAPTURE_BYTES:
            return None, [], "capture-too-large"
    except (OSError, ValueError):
        return None, [], "capture-unreadable"
    if parse_errors:
        return None, [], "capture-jsonl-parse-error"
    if len(records_with_lines) > MAX_CAPTURE_RECORDS:
        return None, [], "capture-too-many-records"
    records: list[tuple[int, dict[str, Any]]] = []
    for line_number, record in records_with_lines:
        if not isinstance(record, dict):
            return None, [], "capture-record-invalid"
        body = record.get("response_body")
        if body not in (None, "") and not isinstance(body, str):
            return None, [], "capture-response-invalid"
        headers = record.get("request_headers")
        if headers not in (None, "") and not isinstance(headers, Mapping):
            return None, [], "capture-record-invalid"
        # Media downloads also carry friendly names, but their binary bodies
        # are not GraphQL evidence. Keep only the actual GraphQL route for
        # both Story extraction and strict target verification.
        path = str(record.get("path") or record.get("url") or "")
        if urlsplit(path).path.rstrip("/").endswith("/graphql"):
            records.append((line_number, record))

    documents: list[Any] = []
    for _, record in records:
        body = record.get("response_body") or ""
        if body:
            decoded = _decode_complete_stream(body)
            if decoded is None:
                return None, [], "capture-response-parse-error"
            documents.extend(decoded)
    try:
        posts = extract_posts(documents, redact=False)
        verification = verify_target_capture(
            posts, records, expected_post_id or None, expected_url or None
        )
    except (TypeError, ValueError, KeyError, re.error):
        return None, [], "capture-analysis-error"
    return verification, posts, None


def _load_capture(
    capture_path: Path,
    min_line: int,
    expected_post_id: str,
    expected_url: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str | None]:
    # Read-only probes must never turn a malformed or changing capture into a
    # navigation failure.  Any unexpected parser/schema exception simply
    # returns false so the PowerShell caller retains its ordinary wait.
    try:
        return _load_capture_impl(capture_path, min_line, expected_post_id, expected_url)
    except Exception:
        return None, [], "capture-analysis-error"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Conservatively prove Facebook target UI readiness.")
    parser.add_argument("--ui", type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--capture", type=Path)
    source.add_argument("--report", type=Path)
    parser.add_argument("--posts", type=Path)
    parser.add_argument("--min-line", type=int, default=1)
    parser.add_argument("--expected-post-id", default="")
    parser.add_argument("--expected-url", default="")
    parser.add_argument("--require-detail", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    ui_bytes = _read_bounded_bytes(args.ui)
    if ui_bytes is None:
        print(_json_result({"ready": False, "reason": "ui-unreadable"}))
        return 0

    source_error: str | None = None
    if args.capture is not None:
        verification, posts, source_error = _load_capture(
            args.capture,
            args.min_line,
            str(args.expected_post_id or "").strip(),
            str(args.expected_url or "").strip(),
        )
        diagnostic_min_line = max(1, int(args.min_line or 1))
    else:
        if args.posts is None:
            source_error = "posts-path-required"
            verification, posts = None, []
        else:
            verification, posts, source_error = _load_report_posts(args.report, args.posts)
        diagnostic_min_line = 1

    if source_error:
        result: dict[str, Any] = {"ready": False, "reason": source_error}
    else:
        result = evaluate_target_ui(
            ui_bytes,
            verification,
            posts,
            expected_post_id=str(args.expected_post_id or "").strip(),
            expected_url=str(args.expected_url or "").strip(),
            require_detail=bool(args.require_detail),
        )
    if isinstance(verification, Mapping):
        matched_line = verification.get("matched_line")
        if matched_line is not None:
            result["matched_line"] = matched_line
        if args.capture is None:
            report_bytes = _read_bounded_bytes(args.report)
            if report_bytes is not None:
                try:
                    report = json.loads(report_bytes.decode("utf-8-sig"))
                    diagnostic_min_line = max(1, int(report.get("min_line") or 1))
                except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
                    pass
    result["verification_min_line"] = diagnostic_min_line
    print(_json_result(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
