"""Fail-closed validation for the analyzer's visible share-user result."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


class ShareValidationError(ValueError):
    """The evidence cannot support a completed share capture."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _s(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _i(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _optional_count(value: Any) -> tuple[int | None, bool]:
    if value in (None, ""):
        return None, False
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None, True
    return (number if number >= 0 else None), number < 0


def _required_count(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ShareValidationError("missing-count", f"share analysis {field} is missing or invalid; reanalyze the capture")
    return value


def _has_share_error(value: Mapping[str, Any]) -> str | None:
    for key in (
        "share_error", "share_errors", "share_query_error", "share_parse_errors",
        "error", "errors", "error_code", "graphql_error_code", "api_error_code",
    ):
        if value.get(key) not in (None, "", [], {}, False, 0):
            return key
    for key in ("rate_limited", "latest_rate_limited", "response_rate_limited",
                "temporary_feature_block", "session_restricted"):
        if value.get(key) is True:
            return key
    for key in ("rate_limit_count", "rate_limited_records", "temporary_feature_block_records",
                "failed_query_records", "unparsed_query_records", "unidentified_edges"):
        if _i(value.get(key)) > 0:
            return key
    return None


def _error_reason(
    report: Mapping[str, Any], enum: Mapping[str, Any], integrity: Mapping[str, Any], pages: Sequence[Any]
) -> str | None:
    if _i(report.get("line_parse_errors")) > 0 or _i(report.get("parse_errors")) > 0:
        return "parse_errors"
    for value in (report, enum, integrity):
        reason = _has_share_error(value)
        if reason:
            return reason
    for page in pages:
        if isinstance(page, Mapping):
            reason = _has_share_error(page)
            if reason:
                return reason
    return None


def _target_id(report: Mapping[str, Any], provided: str) -> str:
    target = report.get("target_verification") or {}
    if not isinstance(target, Mapping) or target.get("status") != "matched":
        raise ShareValidationError("target-unmatched", "share capture target is not matched")
    direct = _s(target.get("matched_post_id"))
    token = _s(target.get("matched_token"))
    if direct:
        if provided and provided != direct:
            raise ShareValidationError("target-mismatch", "share target differs from matched post")
        return direct
    allowed = {_s(value) for value in (target.get("expected_url_tokens") or ()) if _s(value)}
    if not token or token not in allowed or (provided and provided != token):
        raise ShareValidationError("target-mismatch", "share target is not URL-bound")
    return token


def _family_sources(report: Mapping[str, Any], expected: str) -> None:
    families = report.get("source_post_ids_by_family") or {}
    raw = families.get("shares") if isinstance(families, Mapping) else None
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not raw:
        raise ShareValidationError("source-mismatch", "share query source evidence is missing")
    sources = [_s(value) for value in raw]
    if any(not value for value in sources) or set(sources) != {expected}:
        raise ShareValidationError("source-mismatch", "share query source differs from matched post")


def _page_evidence(report: Mapping[str, Any], expected: str) -> tuple[list[Mapping[str, Any]], bool]:
    raw = report.get("share_pages")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not raw:
        raise ShareValidationError("partial", "share pagination has no parsed page")
    pages: list[Mapping[str, Any]] = []
    terminal_seen = False
    for page in raw:
        if not isinstance(page, Mapping):
            raise ShareValidationError("partial", "share pagination page is invalid")
        if _s(page.get("source_post_id")) != expected:
            raise ShareValidationError("page-source-mismatch", "share page source differs from matched post")
        marker = page.get("has_next_page")
        if not isinstance(marker, bool):
            raise ShareValidationError("partial", "share page has no boolean page boundary")
        if terminal_seen and marker:
            raise ShareValidationError("partial", "share pagination reopened after a terminal page")
        terminal_seen = terminal_seen or not marker
        pages.append(page)
    if not terminal_seen or pages[-1].get("has_next_page") is not False:
        raise ShareValidationError("partial", "share pagination has no terminal last page")
    return pages, True


def _stats(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    story_ids: set[str] = set()
    actor_ids: set[str] = set()
    missing_actor_ids = 0
    no_story_id = 0
    for row in rows:
        story = _s(row.get("share_story_id")) or _s(row.get("legacy_api_story_id"))
        if story:
            story_ids.add(story)
        else:
            no_story_id += 1
        actor = _s(row.get("actor_id"))
        if actor:
            actor_ids.add(actor)
        else:
            missing_actor_ids += 1
    unique_stories = len(story_ids) + no_story_id
    return {
        "story_record_count": len(rows),
        "unique_story_count": unique_stories,
        "duplicate_story_records": max(0, len(rows) - unique_stories),
        "unique_actor_count": len(actor_ids),
        "actor_id_record_count": len(rows) - missing_actor_ids,
        "missing_actor_id_records": missing_actor_ids,
    }


def validate_share_result(
    report: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    matched_post_id: str | None = None,
) -> dict[str, Any]:
    """Validate one analyzed share capture and return completion metadata.

    Rows stay story-level: multiple stories by one actor remain records, while
    ``stats.unique_actor_count`` counts distinct actor IDs.  A terminal query
    with zero edges/rows is a valid, distinct ``empty-lists`` result.
    """
    if not isinstance(report, Mapping):
        raise ShareValidationError("invalid-report", "share analysis report is not an object")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or any(not isinstance(row, Mapping) for row in rows):
        raise ShareValidationError("invalid-rows", "share rows are not a list of objects")
    expected = _target_id(report, _s(matched_post_id))
    enum = ((report.get("enumeration_status") or {}).get("shares") or {})
    integrity = report.get("share_integrity") or {}
    if not isinstance(enum, Mapping) or not isinstance(integrity, Mapping):
        raise ShareValidationError("missing-enumeration", "share enumeration evidence is missing")
    query_records = _i(enum.get("query_records"))
    if query_records <= 0:
        raise ShareValidationError("query-not-triggered", "share query was not triggered")
    if not integrity or not all(key in integrity for key in
            ("query_records", "failed_query_records", "unparsed_query_records", "unidentified_edges", "last_query_line")):
        raise ShareValidationError("missing-integrity", "share query audit is missing; reanalyze the capture")
    if _i(integrity.get("query_records"), -1) != query_records:
        raise ShareValidationError("capture-error", "share query audit count is inconsistent")
    status = _s(enum.get("status"))
    classification = _s(enum.get("classification"))
    if status in {"query-not-triggered", "entry-or-query-failed", "no-share-entry"} or classification in {
        "entry-or-query-failed", "no-share-entry"
    }:
        raise ShareValidationError("query-not-triggered", "share list/query was not confirmed")
    _family_sources(report, expected)
    row_sources = {_s(row.get("source_post_id")) for row in rows}
    if row_sources and ("" in row_sources or row_sources != {expected}):
        raise ShareValidationError("row-source-mismatch", "share rows contain a different source")
    pages, terminal = _page_evidence(report, expected)
    error = _error_reason(report, enum, integrity, pages)
    if error:
        raise ShareValidationError("capture-error", f"share capture contains {error}")
    if _i(integrity.get("last_query_line"), -1) != max(_i(page.get("line"), -2) for page in pages):
        raise ShareValidationError("partial", "share query tail has no parsed final page")

    stats = _stats(rows)
    report_rows = _required_count(report.get("share_user_records"), "share_user_records")
    enumerated_rows = _required_count(enum.get("enumerated_identity_count"), "enumerated_identity_count")
    if report_rows != enumerated_rows or report_rows != stats["story_record_count"]:
        raise ShareValidationError("capture-error", "share report and CSV story-record counts disagree")
    if not rows and any(_i(page.get("edge_count"), -1) != 0 for page in pages):
        raise ShareValidationError("capture-error", "terminal share page has unparsed identities")
    raw_total = enum.get("reported_total_count", enum.get("ui_total_count"))
    reported_total, invalid_total = _optional_count(raw_total)
    if invalid_total:
        raise ShareValidationError("invalid-count", "share total count is invalid")
    gap = max(0, reported_total - stats["story_record_count"]) if reported_total is not None else None
    empty = not rows
    return {
        "outcome": "empty-lists" if empty else "users",
        "status": "empty-terminal" if empty else "complete-terminal",
        "completion_claim_allowed": True,
        "terminal_page_seen": terminal,
        "open_tail_without_terminal": False,
        "query_records": query_records,
        "page_count": len(pages),
        "reported_total_count": reported_total,
        "identity_gap": gap,
        "limited": bool(gap),
        "stats": stats,
    }
