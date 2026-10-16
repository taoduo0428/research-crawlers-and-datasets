from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import sys
from typing import Sequence

from .auth import capture_login_state
from .crawler import LiepinConfig, LiepinCrawler
from .errors import LiepinCrawlerError
from .state import LiepinStateStore


DEFAULT_OUTPUT_DIR = Path(".crawler-state/liepin")


def _add_transport_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--proxy-policy",
        choices=("auto", "direct", "required"),
        default=os.getenv("CRAWLER_PROXY_POLICY", "auto"),
    )
    parser.add_argument("--proxy-url", default=os.getenv("CRAWLER_PROXY_URL"))
    parser.add_argument(
        "--cookie-file",
        type=Path,
        default=Path(os.environ["LIEPIN_COOKIE_FILE"])
        if os.getenv("LIEPIN_COOKIE_FILE")
        else None,
        help="JSON Cookie list, Cookie mapping, or Playwright storage-state file",
    )
    parser.add_argument("--connect-timeout", type=float, default=5.0)
    parser.add_argument("--read-timeout", type=float, default=20.0)
    parser.add_argument("--retries", type=int, default=2)


def _add_output_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(os.getenv("LIEPIN_OUTPUT_DIR", DEFAULT_OUTPUT_DIR)),
    )


def _config_from_args(args: argparse.Namespace) -> LiepinConfig:
    return LiepinConfig(
        output_dir=args.output_dir,
        per_category=getattr(args, "per_category", 50),
        numeric_salary_only=getattr(args, "numeric_salary_only", False),
        max_pages_per_category=getattr(args, "max_pages_per_category", 20),
        max_categories=getattr(args, "max_categories", None),
        min_taxonomy_categories=getattr(args, "min_taxonomy_categories", 500),
        delay_min=getattr(args, "delay_min", 0.8),
        delay_max=getattr(args, "delay_max", 1.8),
        connect_timeout=args.connect_timeout,
        read_timeout=args.read_timeout,
        retries=args.retries,
        max_attempts_per_category=getattr(args, "max_attempts", 2),
        max_consecutive_failures=getattr(args, "max_consecutive_failures", 5),
        proxy_policy=args.proxy_policy,
        proxy_url=args.proxy_url,
        cookie_file=args.cookie_file,
        cookie_header=os.getenv("LIEPIN_COOKIE"),
        refresh_taxonomy=getattr(args, "refresh_taxonomy", False),
        retry_failed=not getattr(args, "no_retry_failed", False),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m drv_crawler.liepin",
        description="Bounded Liepin official-career-category crawler",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    taxonomy = subparsers.add_parser("taxonomy", help="Fetch or inspect career taxonomy")
    _add_output_option(taxonomy)
    _add_transport_options(taxonomy)
    taxonomy.add_argument("--refresh-taxonomy", action="store_true")
    taxonomy.add_argument("--min-taxonomy-categories", type=int, default=500)

    crawl = subparsers.add_parser("crawl", help="Crawl all pending third-level careers")
    _add_output_option(crawl)
    _add_transport_options(crawl)
    crawl.add_argument("--per-category", type=int, default=50)
    crawl.add_argument("--max-pages-per-category", type=int, default=20)
    salary_mode = crawl.add_mutually_exclusive_group()
    salary_mode.add_argument(
        "--numeric-salary-only",
        dest="numeric_salary_only",
        action="store_true",
        help="Only numeric, non-zero salary jobs count toward and enter the quota",
    )
    salary_mode.add_argument(
        "--include-non-numeric-salary",
        dest="numeric_salary_only",
        action="store_false",
        help="Keep negotiable or missing salary jobs (backward-compatible mode)",
    )
    crawl.set_defaults(numeric_salary_only=False)
    crawl.add_argument(
        "--max-categories",
        type=int,
        help="Bound this invocation; omit to cover every pending career",
    )
    crawl.add_argument("--delay-min", type=float, default=0.8)
    crawl.add_argument("--delay-max", type=float, default=1.8)
    crawl.add_argument("--max-attempts", type=int, default=2)
    crawl.add_argument("--max-consecutive-failures", type=int, default=5)
    crawl.add_argument("--min-taxonomy-categories", type=int, default=500)
    crawl.add_argument("--refresh-taxonomy", action="store_true")
    crawl.add_argument("--no-retry-failed", action="store_true")

    status = subparsers.add_parser("status", help="Show checkpoint coverage")
    _add_output_option(status)

    login = subparsers.add_parser("login", help="Capture a headed browser login state")
    _add_output_option(login)
    login.add_argument("--proxy-url", default=os.getenv("CRAWLER_PROXY_URL"))
    login.add_argument("--wait-seconds", type=int, default=300)
    login.add_argument(
        "--storage-state",
        type=Path,
        help="Defaults to <output-dir>/storage_state.json",
    )
    return parser


def _print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        if args.command == "login":
            state_path = args.storage_state or args.output_dir / "storage_state.json"
            _print_json(
                capture_login_state(
                    state_path,
                    proxy_url=args.proxy_url,
                    wait_seconds=args.wait_seconds,
                )
            )
            return 0

        if args.command == "status":
            state_path = args.output_dir / "checkpoint.sqlite3"
            if not state_path.exists():
                parser.error(f"checkpoint does not exist: {state_path}")
            with LiepinStateStore(state_path) as state:
                _print_json(state.summary())
            return 0

        config = _config_from_args(args)
        with LiepinCrawler(config) as crawler:
            if args.command == "taxonomy":
                categories = crawler.discover_categories(force=args.refresh_taxonomy)
                crawler.state.export_coverage(config.coverage_path)
                _print_json(
                    {
                        "level_1_count": len({item.level_1 for item in categories}),
                        "level_2_count": len(
                            {(item.level_1, item.level_2) for item in categories}
                        ),
                        "level_3_count": len(categories),
                        "coverage_path": os.fspath(config.coverage_path.resolve()),
                    }
                )
                return 0
            summary = crawler.run()
            _print_json(summary.to_dict())
            return 0 if summary.status in {"success", "partial"} else 2
    except LiepinCrawlerError as exc:
        logging.getLogger(__name__).error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
