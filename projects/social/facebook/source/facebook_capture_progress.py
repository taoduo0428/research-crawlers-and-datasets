import argparse
import json
from pathlib import Path

from analyze_facebook_capture import (
    REACTION_QUERY_PREFIXES,
    extract_reactors,
    extract_share_pages,
    extract_shares,
    iter_records,
    record_reaction_id,
    record_source_post_id,
)


SHARE_PREFIX = "ResharesOfContentFeedQuery"
REACTION_PREFIX = REACTION_QUERY_PREFIXES
NULL_REACTION_KEY = "__ALL__"


def _reaction_rate_limit_summary(records):
    limited = [
        (line, record)
        for line, record in records
        if _friendly_name(record).startswith(REACTION_PREFIX)
        and (
            bool(record.get("response_rate_limited"))
            or record.get("response_graphql_error_code") == 1675004
        )
    ]
    if not limited:
        return {
            "rate_limit_count": 0,
            "latest_rate_limited": False,
            "latest_graphql_error_code": None,
            "latest_rate_limit_line": 0,
        }
    line, record = max(limited, key=lambda item: item[0])
    return {
        "rate_limit_count": len(limited),
        "latest_rate_limited": True,
        "latest_graphql_error_code": record.get("response_graphql_error_code") or 1675004,
        "latest_rate_limit_line": int(line),
    }


def _session_restriction_summary(records, successful_family_prefix=None):
    restricted = [
        (line, record)
        for line, record in records
        if bool(record.get("response_temporary_feature_block"))
        or record.get("response_graphql_error_code") == 3252001
        or record.get("response_api_error_code") == 368
    ]
    # A successful page proves that this capture family is currently usable.
    # After that point, an unrelated background query's feature block must not
    # stop pagination; only a block emitted by this family remains actionable.
    if successful_family_prefix:
        restricted = [
            (line, record)
            for line, record in restricted
            if _friendly_name(record).startswith(successful_family_prefix)
        ]
    if not restricted:
        return {
            "session_restricted": False,
            "session_restriction_line": 0,
            "session_restriction_graphql_code": None,
            "session_restriction_api_error_code": None,
            "session_restriction_friendly_name": None,
        }
    line, record = max(restricted, key=lambda item: item[0])
    return {
        "session_restricted": True,
        "session_restriction_line": int(line),
        "session_restriction_graphql_code": record.get("response_graphql_error_code"),
        "session_restriction_api_error_code": record.get("response_api_error_code"),
        "session_restriction_friendly_name": _friendly_name(record),
    }


def _friendly_name(record):
    return record.get("friendly_name") or record.get("request_headers", {}).get("x-fb-friendly-name") or ""


def _latest_source_post_id(records):
    for _, record in reversed(records):
        source_post_id = record_source_post_id(record)
        if source_post_id:
            return str(source_post_id)
    return None


def _source_post_ids(records):
    values = set()
    for _, record in records:
        source_post_id = record_source_post_id(record)
        if source_post_id:
            values.add(str(source_post_id))
    return sorted(values)


def _filter_source(records, source_post_id):
    if not source_post_id:
        return list(records)
    return [
        (line, record)
        for line, record in records
        if str(record_source_post_id(record) or "") == str(source_post_id)
    ]


def _page_summary(pages):
    return {
        "page_count": len(pages),
        "terminal_page_seen": any(page.get("has_next_page") is False for page in pages),
        "open_page_seen": any(page.get("has_next_page") is True for page in pages),
    }


def summarize_share_records(records, source_post_id=None):
    all_family_records = [
        (line, record)
        for line, record in records
        if _friendly_name(record).startswith(SHARE_PREFIX)
    ]
    observed_source_post_ids = _source_post_ids(all_family_records)
    source_post_id = str(source_post_id or "") or _latest_source_post_id(all_family_records)
    family_records = _filter_source(all_family_records, source_post_id)
    shares = extract_shares(family_records, redact=False)
    pages = extract_share_pages(family_records, redact=False)
    summary = {
        "family": "share",
        "source_post_id": source_post_id,
        "observed_source_post_ids": observed_source_post_ids,
        "query_records": len(family_records),
        "user_count": len(shares),
        "latest_line": max((line for line, _ in family_records), default=0),
    }
    # A source-less Share error is still actionable inside this fresh Share
    # window. Do not mix unrelated Reaction failures into Share pagination.
    limited = [
        (line, record) for line, record in all_family_records
        if record.get("response_rate_limited")
        or record.get("response_graphql_error_code") == 1675004
    ]
    summary.update({
        "rate_limit_count": len(limited),
        "latest_rate_limited": bool(limited),
        "latest_rate_limit_line": max((line for line, _ in limited), default=0),
    })
    summary.update(_session_restriction_summary(records, successful_family_prefix=SHARE_PREFIX))
    summary.update(_page_summary(pages))
    return summary


def _reaction_key(value):
    return str(value) if value not in (None, "") else NULL_REACTION_KEY


def summarize_reaction_records(records, source_post_id=None):
    all_family_records = [
        (line, record)
        for line, record in records
        if _friendly_name(record).startswith(REACTION_PREFIX)
    ]
    observed_source_post_ids = _source_post_ids(all_family_records)
    source_post_id = str(source_post_id or "") or _latest_source_post_id(all_family_records)
    family_records = _filter_source(all_family_records, source_post_id)

    groups = {}
    for line, record in family_records:
        key = _reaction_key(record_reaction_id(record))
        groups.setdefault(key, []).append((line, record))

    group_summaries = {}
    for key, group_records in groups.items():
        reactors, pages = extract_reactors(group_records, redact=False)
        group_summary = {
            "reaction_key": key,
            "query_records": len(group_records),
            "user_count": len(reactors),
            "latest_line": max((line for line, _ in group_records), default=0),
        }
        group_summary.update(_page_summary(pages))
        group_summaries[key] = group_summary

    reactors, pages = extract_reactors(family_records, redact=False)
    latest_line = max((line for line, _ in family_records), default=0)
    latest_group_key = None
    if family_records:
        _, latest_record = max(family_records, key=lambda item: item[0])
        latest_group_key = _reaction_key(record_reaction_id(latest_record))

    summary = {
        "family": "reaction",
        "source_post_id": source_post_id,
        "observed_source_post_ids": observed_source_post_ids,
        "query_records": len(family_records),
        "user_count": len(reactors),
        "latest_line": latest_line,
        "latest_group_key": latest_group_key,
        "groups": group_summaries,
    }
    # Rate limiting can arrive on a source-less pagination error response. Keep
    # users/pages source-scoped, but surface the rate-limit state for this bounded
    # Reaction window so the UI orchestrator can stop instead of blindly swiping.
    summary.update(_reaction_rate_limit_summary(all_family_records))
    summary.update(
        _session_restriction_summary(
            records,
            successful_family_prefix=REACTION_PREFIX if pages else None,
        )
    )
    summary.update(_page_summary(pages))
    return summary


def summarize_records(records, family, source_post_id=None):
    if family == "share":
        return summarize_share_records(records, source_post_id)
    if family == "reaction":
        return summarize_reaction_records(records, source_post_id)
    raise ValueError(f"unsupported family: {family}")


def parse_args():
    parser = argparse.ArgumentParser(description="Read-only progress summary for an in-progress Facebook capture JSONL.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--family", required=True, choices=("share", "reaction"))
    parser.add_argument("--max-records", type=int, default=10000)
    parser.add_argument("--source-post-id")
    parser.add_argument("--min-line", type=int, default=1)
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.input.exists():
        print(json.dumps({"family": args.family, "missing_input": True}, ensure_ascii=False))
        return
    records, parse_errors = iter_records(args.input, args.max_records, min_line=args.min_line)
    # Source/user data stays bounded by --min-line. Before a successful family
    # page, retain a run-wide feature block as a conservative stop signal. Once
    # a page succeeds, only a block from that same query family is actionable.
    session_records, session_parse_errors = iter_records(args.input, args.max_records, min_line=1)
    summary = summarize_records(records, args.family, args.source_post_id)
    successful_prefix = None
    if summary.get("page_count", 0) > 0:
        successful_prefix = REACTION_PREFIX if args.family == "reaction" else SHARE_PREFIX
    summary.update(_session_restriction_summary(session_records, successful_prefix))
    summary["parse_errors"] = max(parse_errors, session_parse_errors)
    print(json.dumps(summary, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
