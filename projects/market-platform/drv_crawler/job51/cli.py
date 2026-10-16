from __future__ import annotations

import argparse
from importlib import metadata
import json
import logging
from pathlib import Path
import sys
from typing import Any

from .crawler import (
    Job51Crawler,
    Job51Error,
    resolve_proxy_url,
    save_login_state,
)
from .models import CrawlConfig, PACKAGE_DIR
from .state import CrawlState


PATH_KEYS = {
    "storage_state_path",
    "runtime_session_state_path",
    "category_snapshot_path",
    "state_db_path",
    "output_path",
    "coverage_path",
}


def _add_browser_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--proxy-policy", choices=("auto", "off", "required"))
    parser.add_argument("--proxy-url")
    parser.add_argument("--browser-channel")
    parser.add_argument("--storage-state-path", type=Path)
    parser.add_argument("--runtime-session-state-path", type=Path)
    parser.add_argument("--timeout-ms", type=int)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--headless", dest="headless", action="store_true")
    mode.add_argument("--headed", dest="headless", action="store_false")
    parser.set_defaults(headless=None)


def _load_config_file(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    path = path.expanduser().resolve()
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("config file must contain a JSON object")
    for key in PATH_KEYS:
        if key in value and value[key] is not None:
            candidate = Path(value[key]).expanduser()
            if not candidate.is_absolute():
                candidate = path.parent / candidate
            value[key] = candidate.resolve()
    return value


def _config_from_args(args: argparse.Namespace) -> CrawlConfig:
    value = _load_config_file(getattr(args, "config", None))
    for key in CrawlConfig.__dataclass_fields__:
        if not hasattr(args, key):
            continue
        override = getattr(args, key)
        if override is not None:
            value[key] = override
    return CrawlConfig.from_mapping(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="job51-crawler",
        description="Bounded, resumable 51job official-category sampler",
    )
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="check dependencies, proxy, and local state")
    doctor.add_argument("--config", type=Path)
    _add_browser_options(doctor)

    login = subparsers.add_parser("login", help="save an interactive browser login state")
    login.add_argument("--config", type=Path)
    login.add_argument("--wait-seconds", type=int)
    _add_browser_options(login)

    categories = subparsers.add_parser(
        "categories", help="refresh and export the live official function tree"
    )
    categories.add_argument("--config", type=Path)
    categories.add_argument(
        "--output",
        type=Path,
        default=PACKAGE_DIR / "data" / "categories.json",
    )
    _add_browser_options(categories)

    run = subparsers.add_parser("run", help="sample all pending official leaf categories")
    run.add_argument("--config", type=Path)
    run.add_argument("--city-code")
    run.add_argument("--per-category-limit", type=int)
    run.add_argument("--max-pages-per-category", type=int)
    salary_mode = run.add_mutually_exclusive_group()
    salary_mode.add_argument(
        "--numeric-salary-only",
        dest="numeric_salary_only",
        action="store_true",
        help="count and export only records with a non-zero numeric salary bound",
    )
    salary_mode.add_argument(
        "--include-non-numeric-salary",
        dest="numeric_salary_only",
        action="store_false",
        help="preserve legacy behavior where any matched job consumes the quota",
    )
    run.set_defaults(numeric_salary_only=None)
    run.add_argument("--max-codes", type=int)
    run.add_argument("--request-retries", type=int)
    run.add_argument("--max-category-attempts", type=int)
    run.add_argument("--delay-min-seconds", type=float)
    run.add_argument("--delay-max-seconds", type=float)
    run.add_argument("--category-snapshot-path", type=Path)
    run.add_argument("--state-db-path", type=Path)
    run.add_argument("--output-path", type=Path)
    run.add_argument("--coverage-path", type=Path)
    _add_browser_options(run)

    status = subparsers.add_parser("status", help="show checkpoint coverage")
    status.add_argument(
        "--state-db-path",
        type=Path,
        default=PACKAGE_DIR / ".state" / "crawl.sqlite3",
    )
    return parser


def _doctor(config: CrawlConfig) -> dict[str, Any]:
    try:
        playwright_version = metadata.version("playwright")
    except metadata.PackageNotFoundError:
        playwright_version = None
    proxy_error = None
    try:
        proxy = resolve_proxy_url(config)
    except Job51Error as exc:
        proxy = None
        proxy_error = str(exc)
    return {
        "python": sys.version.split()[0],
        "playwright": playwright_version,
        "proxy_policy": config.proxy_policy,
        "proxy_available": proxy is not None,
        "proxy_error": proxy_error,
        "storage_state_exists": config.storage_state_path.exists(),
        "runtime_session_state_exists": config.runtime_session_state_path.exists(),
        "checkpoint_exists": config.state_db_path.exists(),
        "ready": playwright_version is not None and proxy_error is None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        if args.command == "status":
            state_path = args.state_db_path.expanduser().resolve()
            if not state_path.exists():
                raise ValueError(f"checkpoint does not exist: {state_path}")
            with CrawlState(state_path) as state:
                value = state.coverage()
        else:
            config = _config_from_args(args)
            if args.command == "doctor":
                value = _doctor(config)
            elif args.command == "login":
                cookie_count = save_login_state(config, args.wait_seconds)
                value = {
                    "storage_state_path": str(config.storage_state_path),
                    "cookie_count": cookie_count,
                }
            elif args.command == "categories":
                output_path = args.output.resolve()
                snapshot = Job51Crawler(config).export_categories(output_path)
                value = {
                    "output_path": str(output_path),
                    "captured_at": snapshot["captured_at"],
                    "level_1_count": snapshot["level_1_count"],
                    "level_2_path_count": snapshot["level_2_path_count"],
                    "level_3_path_count": snapshot["level_3_path_count"],
                    "unique_search_code_count": snapshot[
                        "unique_search_code_count"
                    ],
                }
            elif args.command == "run":
                value = Job51Crawler(config).run().to_dict()
            else:
                parser.error(f"unknown command: {args.command}")
                return 2
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0
    except (Job51Error, OSError, ValueError, json.JSONDecodeError) as exc:
        logging.getLogger("job51").error("%s: %s", type(exc).__name__, exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
