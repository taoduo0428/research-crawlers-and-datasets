from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any

from .browser import acquire_storage_state
from .core import CrawlerError, ProjectConfig, ResilientHttpClient, atomic_write_json
from .recruitment import (
    RECRUITMENT_SITES,
    recruitment_doctor,
    run_recruitment,
    run_recruitment_auth,
)
from .runner import run_sites
from .publication import PublicationFilter


PUBLICATION_FILTER_SITES = {"epwk", "zbj", "shixian"}


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def iso_date_text(value: str) -> str:
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc
    return value


def add_publication_filter_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--recent-months",
        type=positive_int,
        help="strictly keep records published within N calendar months",
    )
    group.add_argument(
        "--published-since",
        type=iso_date_text,
        help="strictly keep records published on or after YYYY-MM-DD",
    )
    parser.add_argument(
        "--as-of",
        type=iso_date_text,
        help="freeze relative publication dates at YYYY-MM-DD",
    )


def configure_logging(config: ProjectConfig) -> None:
    section = config.section("logging")
    level = getattr(logging, str(section.get("level", "INFO")).upper(), logging.INFO)
    log_path = Path(str(section.get("file", "logs/crawler.log")))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
        force=True,
    )


def select_sites(config: ProjectConfig, site: str) -> list[str]:
    if site == "all":
        return config.site_names
    if site not in config.site_names:
        raise SystemExit(f"Unknown or disabled site: {site}")
    return [site]


def run_collection(args: argparse.Namespace, config: ProjectConfig, *, mode: str) -> int:
    if mode == "sample" and not 1 <= args.limit <= 50:
        raise SystemExit("sample --limit must be between 1 and 50")
    if mode == "collect" and args.limit > 50 and not args.approved:
        raise SystemExit(
            "Collection above 50 records/site requires --approved after reviewing the sample"
        )
    if args.detail_limit is not None and not 1 <= args.detail_limit <= 200:
        raise SystemExit("--detail-limit must be between 1 and 200")
    sites = select_sites(config, args.site)
    publication_filter = None
    if args.as_of is not None and (
        args.recent_months is None and args.published_since is None
    ):
        raise SystemExit("--as-of requires --recent-months or --published-since")
    if args.recent_months is not None or args.published_since is not None:
        unsupported = sorted(set(sites) - PUBLICATION_FILTER_SITES)
        if unsupported:
            raise SystemExit(
                "Publication filtering is supported only for epwk, zbj and shixian; "
                f"unsupported: {', '.join(unsupported)}"
            )
        if "epwk" in sites and args.no_details:
            raise SystemExit("EPWK publication filtering requires task detail pages")
        try:
            publication_filter = PublicationFilter(
                recent_months=args.recent_months,
                published_since=args.published_since,
                as_of=date.fromisoformat(args.as_of) if args.as_of else None,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    run_dir, results, summary = run_sites(
        config,
        site_names=sites,
        limit=args.limit,
        with_details=not args.no_details,
        mode=mode,
        numeric_price_only=args.numeric_price_only,
        publication_filter=publication_filter,
        complete_publication_window=(
            mode == "collect" and publication_filter is not None
        ),
        detail_limit=args.detail_limit,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"OUTPUT_DIR={run_dir}")
    if all(result.status == "success" for result in results):
        return 0
    if any(result.records for result in results):
        return 2
    return 1


def doctor(config: ProjectConfig) -> int:
    dependencies = [
        "requests",
        "bs4",
        "yaml",
        "playwright",
        "curl_cffi",
        "certifi",
        "openpyxl",
        "websocket",
    ]
    checks: dict[str, Any] = {
        "config": str(config.path),
        "sites": config.site_names,
        "dependencies": {
            name: importlib.util.find_spec(name) is not None for name in dependencies
        },
    }
    try:
        with ResilientHttpClient(config) as client:
            checks["proxy"] = {
                "enabled": bool(client.proxy_url),
                "url": "configured" if client.proxy_url else None,
            }
    except Exception as exc:
        checks["proxy"] = {"error": type(exc).__name__, "message": str(exc)}
    state_checks: dict[str, bool] = {}
    for site_name in ("boss", "epwk", "zbj"):
        site = config.site(site_name)
        if str(site.get("auth_mode", "")) == "external_edge_cdp":
            profile_dir = Path(
                str(
                    site.get(
                        "auth_profile_dir",
                        ".crawler-state/boss-manual-edge-profile",
                    )
                )
            )
            state_checks[site_name] = (
                profile_dir / "Default" / "Network" / "Cookies"
            ).exists()
        else:
            state_path = site.get("storage_state")
            state_checks[site_name] = bool(
                state_path and Path(str(state_path)).exists()
            )
    checks["storage_state_present"] = state_checks
    try:
        checks["recruitment"] = recruitment_doctor(config)
    except (OSError, ValueError) as exc:
        checks["recruitment"] = {
            "error": type(exc).__name__,
            "message": str(exc),
        }
    checks["ok"] = (
        all(checks["dependencies"].values())
        and "error" not in checks["proxy"]
        and "error" not in checks["recruitment"]
        and bool(checks["recruitment"].get("ok", False))
    )
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return 0 if checks["ok"] else 1


def validate_run(run_dir: Path) -> int:
    report_path = run_dir / "run-report.json"
    if not report_path.exists():
        raise SystemExit(f"Missing run report: {report_path}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    findings: list[dict[str, Any]] = []
    for site in report.get("sites", []):
        site_name = site["site_name"]
        data_path = run_dir / site_name / "records.jsonl"
        count = 0
        invalid = 0
        if data_path.exists():
            with data_path.open("r", encoding="utf-8", newline="") as handle:
                for line_no, line in enumerate(handle, 1):
                    if not line.strip():
                        continue
                    count += 1
                    try:
                        item = json.loads(line)
                        if not item.get("title_raw") or not item.get("price_evidence"):
                            invalid += 1
                    except json.JSONDecodeError:
                        invalid += 1
        structural_valid = invalid == 0 and count == site.get("quality", {}).get(
            "record_count"
        )
        target_met = bool(site.get("quality", {}).get("quality_passed"))
        findings.append(
            {
                "site_name": site_name,
                "records": count,
                "invalid_core_records": invalid,
                "structurally_valid": structural_valid,
                "target_met": target_met,
                "valid": structural_valid and target_met,
            }
        )
    result = {
        "run_dir": str(run_dir),
        "sites": findings,
        "ok": all(x["valid"] for x in findings),
    }
    atomic_write_json(run_dir / "validation.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DRV outsourcing and recruitment crawler")
    parser.add_argument("--config", default="crawler.config.yaml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="check dependencies, proxy, config and session state")

    sample = subparsers.add_parser("sample", help="collect at most 50 records per site")
    sample.add_argument("--site", default="all")
    sample.add_argument("--limit", type=int, default=50)
    sample.add_argument(
        "--detail-limit",
        type=int,
        help="bounded maximum detail pages to fetch (default: site configuration)",
    )
    sample.add_argument("--no-details", action="store_true")
    sample.add_argument(
        "--numeric-price-only",
        action="store_true",
        help="keep only records whose price text contains a nonzero digit",
    )
    add_publication_filter_arguments(sample)

    collect = subparsers.add_parser("collect", help="bounded collection after sample approval")
    collect.add_argument("--site", default="all")
    collect.add_argument("--limit", type=int, required=True)
    collect.add_argument(
        "--detail-limit",
        type=int,
        help="bounded maximum detail pages to fetch (default: site configuration)",
    )
    collect.add_argument("--approved", action="store_true")
    collect.add_argument("--no-details", action="store_true")
    collect.add_argument(
        "--numeric-price-only",
        action="store_true",
        help="keep only records whose price text contains a nonzero digit",
    )
    add_publication_filter_arguments(collect)

    jobs = subparsers.add_parser(
        "jobs",
        help="sample official job categories from BOSS, 51job and Liepin",
    )
    jobs.add_argument("--site", choices=["all", *RECRUITMENT_SITES], default="all")
    jobs.add_argument(
        "--per-category",
        type=positive_int,
        help="records to retain per official leaf category",
    )
    jobs.add_argument(
        "--max-categories",
        type=positive_int,
        help=(
            "process only this many pending units per site in this batch "
            "(BOSS leaf codes, 51job search codes, Liepin career paths); "
            "omit it to run the checkpointed full probe with automatic cooldown resume"
        ),
    )
    jobs.add_argument(
        "--numeric-salary-only",
        action="store_true",
        default=None,
        help=(
            "retain only jobs with a nonzero numeric salary; this mode uses a "
            "separate resumable scope"
        ),
    )
    jobs.add_argument(
        "--output-dir",
        type=Path,
        help="parent directory for the immutable run snapshot",
    )

    auth = subparsers.add_parser(
        "auth", help="headed human login/verification and atomic storage-state save"
    )
    auth.add_argument(
        "--site",
        choices=["boss", "epwk", "zbj", "job51", "liepin"],
        required=True,
    )
    auth.add_argument("--wait-seconds", type=int, default=180)
    auth.add_argument(
        "--keep-open",
        action="store_true",
        help="keep the headed auth browser open after the session state is saved",
    )

    validate = subparsers.add_parser("validate", help="validate an existing output directory")
    validate.add_argument("run_dir", type=Path)
    return parser


def _recruitment_auth_succeeded(result: dict[str, Any]) -> bool:
    if int(result.get("return_code", 0)) != 0:
        return False
    if result.get("login_verified") is False:
        return False
    if "cookie_count" in result and int(result.get("cookie_count") or 0) < 1:
        return False
    return True


def main() -> int:
    args = build_parser().parse_args()
    config = ProjectConfig(args.config)
    configure_logging(config)
    if args.command == "doctor":
        return doctor(config)
    if args.command == "sample":
        return run_collection(args, config, mode="sample")
    if args.command == "collect":
        return run_collection(args, config, mode="collect")
    if args.command == "jobs":
        try:
            run_dir, summary = run_recruitment(
                config,
                site_name=args.site,
                per_category=args.per_category,
                max_categories=args.max_categories,
                numeric_salary_only=args.numeric_salary_only,
                output_root=args.output_dir,
            )
        except (CrawlerError, OSError, ValueError) as exc:
            logging.getLogger("drv_crawler.recruitment").error("%s", exc)
            print(f"JOBS_FAILED: {exc}")
            return 1
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"OUTPUT_DIR={run_dir}")
        totals = summary["totals"]
        if totals["failed"] == 0 and totals["partial"] == 0:
            return 0
        return 2 if totals["records"] else 1
    if args.command == "auth":
        if args.site in RECRUITMENT_SITES:
            try:
                result = run_recruitment_auth(
                    config,
                    args.site,
                    wait_seconds=args.wait_seconds,
                )
            except (OSError, ValueError, RuntimeError) as exc:
                logging.getLogger("drv_crawler.auth").error("%s", exc)
                print(f"AUTH_NOT_SAVED: {exc}")
                return 2
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if _recruitment_auth_succeeded(result) else 2
        try:
            path = acquire_storage_state(
                config,
                args.site,
                wait_seconds=args.wait_seconds,
                keep_open=args.keep_open,
            )
        except CrawlerError as exc:
            logging.getLogger("drv_crawler.auth").error("%s", exc)
            print(f"AUTH_NOT_SAVED: {exc}")
            return 2
        print(f"Saved session/profile state: {path}")
        return 0
    if args.command == "validate":
        return validate_run(args.run_dir)
    return 1
