#!/usr/bin/env python3
"""Small, read-only live checks for the archived Amazon/Jiimore/SellerSprite project.

Never uses saved browser profiles, session cookies, passwords, CAPTCHA solvers,
rotating proxies, or old output files. Only 1-3 explicit ASINs are allowed.
The results are field counts/status summaries, not saved third-party content.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
import requests

PROJECT = Path(__file__).resolve().parents[1]
REPOSITORY = PROJECT.parents[2]
ASIN_PATTERN = re.compile(r"^[A-Z0-9]{10}$")
MAX_BYTES = 9_000_000
HOSTS = ("www.amazon.com", "www.sellersprite.com", "www.jiimore.com")
DEFAULT_ASIN = "B005G9E98O"  # Present in the archived products table
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/155.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json;q=0.9",
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch(session, method: str, url: str, *, params=None, data=None, headers=None):
    """Read one bounded response; never follow login/captcha redirects."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc not in HOSTS or parsed.username:
        raise ValueError("Only verified HTTPS platform endpoints are allowed")
    # Do not reuse even anonymous cookies that another endpoint may have set.
    if hasattr(session, "cookies"):
        session.cookies.clear()
    with session.request(
        method, url, params=params, data=data, headers=headers, timeout=(5, 18),
        allow_redirects=False, stream=True,
    ) as response:
        chunks = []
        total = 0
        for chunk in response.iter_content(chunk_size=65536):
            total += len(chunk)
            if total > MAX_BYTES:
                raise ValueError("Live response exceeds size limit")
            chunks.append(chunk)
        return response.status_code, b"".join(chunks)


def json_object(raw: bytes) -> dict:
    value = json.loads(raw)
    return value if isinstance(value, dict) else {}


def scrape_amazon(raw: bytes) -> dict:
    page = BeautifulSoup(raw, "html.parser")
    title = page.select_one("#productTitle")
    price = (page.select_one("#corePrice_feature_div .a-offscreen")
             or page.select_one(".a-price .a-offscreen"))
    stars = page.select_one("#acrPopover")
    count = page.select_one("#acrCustomerReviewText")
    found = {
        "title": title.get_text(" ", strip=True)[:180] if title else "",
        "price": price.get_text(" ", strip=True)[:45] if price else "",
        "rating": (stars.get("title") or stars.get_text(" ", strip=True))[:70] if stars else "",
        "review_count": count.get_text(" ", strip=True)[:90] if count else "",
    }
    found["challenge_page"] = bool(
        page.select_one("form[action*='captcha'],#captchacharacters")
        or re.search(r"robot check|validatecaptcha", str(page.title or ""), re.I)
    )
    found["fields_found"] = sum(bool(found[k]) for k in (
        "title", "price", "rating", "review_count"
    ))
    found["looks_like_product_page"] = bool(found["title"]) and not found["challenge_page"]
    return found


def review_summary(raw: bytes) -> dict:
    data = json_object(raw).get("data")
    data = data if isinstance(data, dict) else {}
    months = data.get("daltaMonth") or []
    delta = data.get("dalta") or []
    rate = data.get("rate") or []
    if not all(isinstance(x, list) for x in (months, delta, rate)):
        return {"usable": False, "reason": "unexpected_field_types"}
    valid = [(i, m) for i, m in enumerate(months)
             if isinstance(m, str) and re.fullmatch(r"\d{6}", m)]
    latest_nonempty = [
        m for i, m in valid if i < len(delta) and delta[i] is not None
        and str(delta[i]).strip() != ""
    ]
    return {
        "usable": bool(valid and latest_nonempty),
        "months_returned": len(months),
        "first_month": valid[0][1] if valid else None,
        "last_month": valid[-1][1] if valid else None,
        "last_month_with_review_delta": latest_nonempty[-1] if latest_nonempty else None,
        "nonempty_monthly_deltas": len(latest_nonempty),
        "nonempty_monthly_rates": sum(x is not None and str(x).strip() != ""
                                      for x in rate),
    }


def keepa_summary(raw: bytes) -> dict:
    data = json_object(raw).get("data")
    info = data.get("keepa") if isinstance(data, dict) else {}
    info = info if isinstance(info, dict) else {}
    timestamps = info.get("times") or []
    series = info.get("keepa") or {}
    series = series if isinstance(series, dict) else {}
    if not isinstance(timestamps, list):
        return {"usable": False, "reason": "unexpected_timestamps"}
    lengths = {}
    nonempty = {}
    last_filled = {}
    for key in ("rating", "reviews", "price"):
        values = series.get(key)
        values = values if isinstance(values, list) else []
        lengths[key] = len(values)
        useful = [i for i, value in enumerate(values[:len(timestamps)])
                  if value is not None and str(value).strip() not in ("", "-1", "null")]
        nonempty[key] = len(useful)
        last_filled[key] = (
            str(timestamps[useful[-1]])[:19] if useful else None
        )
    return {
        "usable": bool(timestamps and any(
            lengths[key] == len(timestamps) and nonempty[key] > 0
            for key in lengths
        )),
        "points": len(timestamps),
        "first_timestamp": str(timestamps[0])[:19] if timestamps else None,
        "last_timestamp": str(timestamps[-1])[:19] if timestamps else None,
        "series_lengths": lengths,
        "series_nonempty": nonempty,
        "series_last_nonempty_timestamp": last_filled,
        "currency_label": str(info.get("currency") or "")[:25],
    }


def monthly_summary(raw: bytes) -> dict:
    chart = json_object(raw).get("data")
    chart = chart.get("chartData") if isinstance(chart, dict) else None
    return {"usable": isinstance(chart, dict) and len(chart) > 0,
            "months": len(chart) if isinstance(chart, dict) else 0,
            "returned_empty_json": raw.strip() == b"{}"}


def single_probe(session, source: str, asin: str) -> dict:
    response = {"source": source, "asin": asin, "success": False}
    if source == "amazon":
        method, url = "GET", f"https://www.amazon.com/dp/{asin}"
        opts = {"params": {"language": "en_US"}}
        parser = scrape_amazon
    elif source == "sellersprite_reviews":
        method, url = "POST", f"https://www.sellersprite.com/v3/api/trends/reviews/1/{asin}"
        opts = {"data": "{}", "headers": {"Content-Type": "application/json"}}
        parser = review_summary
    elif source == "sellersprite_keepa":
        method, url = "GET", f"https://www.sellersprite.com/v2/keepa/subTrend/us/{asin}"
        opts = {"params": {"parentModule": "ARA"}}
        parser = keepa_summary
    elif source == "sellersprite_sales":
        method, url = "POST", "https://www.sellersprite.com/v2/competitor-lookup/chart-monthly.json"
        opts = {"data": {"asin": asin, "marketId": "1"}}
        parser = monthly_summary
    elif source == "jiimore_weekly":
        method, url = "POST", (
            "https://www.jiimore.com/api/amz-aba/aba/opportunityExplorer/"
            f"asin/{asin}/ATVPDKIKX0DER/week"
        )
        opts = {}
        parser = json_object
    else:
        raise ValueError(f"Unknown source {source}")

    try:
        http, raw = fetch(session, method, url, **opts)
        response["http"] = http
        if http == 401:
            response["status"] = "login_required"
        elif http == 403:
            response["status"] = "permission_denied"
        elif http in (202, 429):
            response["status"] = "not_accepted_or_limited"
        elif http != 200:
            response["status"] = "http_error"
        elif source == "jiimore_weekly":
            reply = json_object(raw)
            trends = reply.get("trends")
            response["success"] = isinstance(trends, list) and len(trends) > 0
            response["trends_length"] = len(trends) if isinstance(trends, list) else 0
            response["status"] = "usable" if response["success"] else "not_usable_without_session"
        else:
            fields = parser(raw)
            response.update(fields)
            if source == "amazon":
                response["success"] = bool(fields["looks_like_product_page"])
            else:
                response["success"] = bool(fields["usable"])
            response["status"] = "usable" if response["success"] else "no_usable_data"
    except (requests.RequestException, ValueError, OSError) as error:
        response["status"] = "network_or_parse_error"
        response["error_type"] = type(error).__name__
    return response


SOURCES = ("amazon", "sellersprite_reviews", "sellersprite_keepa",
           "sellersprite_sales", "jiimore_weekly")


def run(asins: list[str], sources: list[str], delay: float = 0.6) -> dict:
    if not 1 <= len(asins) <= 3 or delay < 0.5:
        raise ValueError("At most 3 ASINs; at least 0.5 seconds between requests")
    for asin in asins:
        if not ASIN_PATTERN.fullmatch(asin):
            raise ValueError("ASIN must be exactly ten letters/digits")
    session = requests.Session()
    session.headers.update(HEADERS)
    session.cookies.clear()
    results = []
    for asin in asins:
        for source in sources:
            results.append(single_probe(session, source, asin))
            time.sleep(delay)
    return {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "live_network_requests": len(results),
        "used_saved_cookies": False,
        "used_login": False,
        "captcha_solver": False,
        "sites": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Small current-site tests; never changes archived CSVs")
    parser.add_argument("--asin", action="append", default=[], help="Repeat once/twice; default B005G9E98O")
    parser.add_argument("--site", choices=(*SOURCES, "all"), default="all")
    parser.add_argument("--delay", type=float, default=0.6)
    parser.add_argument("--output", type=Path, help="Optional JSON report; must be outside project folder")
    args = parser.parse_args()
    asins = args.asin or [DEFAULT_ASIN]
    sources = list(SOURCES) if args.site == "all" else [args.site]
    if args.output:
        destination = args.output.resolve()
        if destination.is_relative_to(REPOSITORY.resolve()):
            parser.error("Report must be stored outside the Git repository")
        if destination.exists():
            parser.error("Output already exists; choose another filename")
    try:
        report = run(asins, sources, args.delay)
    except ValueError as error:
        parser.error(str(error))
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    print(serialized)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as output:
            output.write(serialized + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
