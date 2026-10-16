from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Any

from drv_crawler.job51.crawler import Job51Crawler, parse_search_payload, redact_message
from drv_crawler.job51.models import CrawlConfig


DEFAULT_CODES = ("A0N7", "6010", "6001", "5105", "4924")
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _probe_code(crawler: Job51Crawler, playwright: Any, code: str) -> dict[str, Any]:
    started = time.monotonic()
    browser = context = None
    result: dict[str, Any] = {"code": code}
    try:
        browser, context, page = crawler._launch(playwright)
        crawler._bootstrap_page(page)
        bootstrapped_at = time.monotonic()
        with page.expect_response(
            lambda response: crawler._search_response_matches(
                response, code, crawler.config.city_code, 1
            ),
            timeout=crawler.config.timeout_ms,
        ) as response_info:
            invocation = page.evaluate(
                """
                ({functionCode, cityCode}) => {
                  let search = null;
                  for (const element of document.querySelectorAll('*')) {
                    const component = element.__vue__;
                    if (component && component.$options &&
                        component.$options.name === 'PCSearch') {
                      search = component;
                      break;
                    }
                  }
                  if (!search || typeof search.getSearch !== 'function') {
                    throw new Error('PCSearch.getSearch is unavailable');
                  }
                  search.searchParams.function = functionCode;
                  search.searchParams.jobArea = cityCode;
                  search.searchParams.jobArea2 = '';
                  search.searchParams.pageNum = 1;
                  search.searchParams.requestId = '';
                  window.__job51SearchProbe = {
                    state: 'pending',
                    settledAt: null,
                    error: null
                  };
                  let returned;
                  try {
                    returned = search.getSearch('');
                  } catch (error) {
                    window.__job51SearchProbe = {
                      state: 'threw',
                      settledAt: Date.now(),
                      error: String(error)
                    };
                    throw error;
                  }
                  Promise.resolve(returned).then(
                    () => {
                      window.__job51SearchProbe.state = 'resolved';
                      window.__job51SearchProbe.settledAt = Date.now();
                    },
                    error => {
                      window.__job51SearchProbe.state = 'rejected';
                      window.__job51SearchProbe.settledAt = Date.now();
                      window.__job51SearchProbe.error = String(error);
                    }
                  );
                  return {
                    thenable: Boolean(returned && typeof returned.then === 'function')
                  };
                }
                """,
                {"functionCode": code, "cityCode": crawler.config.city_code},
            )
        response = response_info.value
        response_at = time.monotonic()
        page.wait_for_timeout(2_000)
        promise = page.evaluate("() => window.__job51SearchProbe")
        body_text = response.text()
        response_details = {
            "http_status": response.status,
            "content_type": response.headers.get("content-type"),
            "body_chars": len(body_text),
            "thenable": invocation["thenable"],
            "promise_state_after_2s": promise["state"],
            "promise_error": (
                redact_message(promise.get("error")) if promise.get("error") else None
            ),
            "bootstrap_seconds": round(bootstrapped_at - started, 3),
            "response_seconds": round(response_at - bootstrapped_at, 3),
        }
        try:
            parsed = parse_search_payload(json.loads(body_text))
        except Exception as exc:
            body_lower = body_text[:4_096].lower()
            result.update(
                {
                    "outcome": "response_invalid_json",
                    **response_details,
                    "parse_error": redact_message(exc),
                    "waf_marker": any(
                        marker in body_lower
                        for marker in (
                            "_waf_bd8ce2ce37",
                            "aliyun_waf_",
                            'id="renderdata"',
                        )
                    ),
                }
            )
            return result
        result.update(
            {
                "outcome": "response",
                **response_details,
                "items": len(parsed.items),
                "total_count": parsed.total_count,
            }
        )
    except Exception as exc:
        result.update(
            {
                "outcome": "error",
                "error_type": type(exc).__name__,
                "error": redact_message(exc),
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
        )
    finally:
        if context is not None:
            context.close()
        if browser is not None:
            browser.close()
    return result


def _probe_crawler_path(
    crawler: Job51Crawler, playwright: Any, code: str
) -> dict[str, Any]:
    started = time.monotonic()
    browser = context = None
    try:
        browser, context, page = crawler._launch(playwright)
        crawler._bootstrap_page(page)
        search_page = crawler._search_once(page, code, 1)
        return {
            "code": code,
            "outcome": "response",
            "items": len(search_page.items),
            "total_count": search_page.total_count,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
    except Exception as exc:
        return {
            "code": code,
            "outcome": "error",
            "error_type": type(exc).__name__,
            "error": redact_message(exc),
            "retry_after": getattr(exc, "retry_after", None),
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
    finally:
        if context is not None:
            context.close()
        if browser is not None:
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bounded, state-free 51job getSearch/response diagnostic."
    )
    parser.add_argument("codes", nargs="*", default=list(DEFAULT_CODES))
    parser.add_argument("--timeout-ms", type=int, default=30_000)
    parser.add_argument("--mode", choices=("observe", "crawler"), default="observe")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument(
        "--proxy-policy", choices=("auto", "off", "required"), default="auto"
    )
    parser.add_argument("--proxy-url", default=None)
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    config = CrawlConfig(
        city_code="000000",
        per_category_limit=1,
        max_pages_per_category=1,
        request_retries=0,
        timeout_ms=args.timeout_ms,
        delay_min_seconds=0,
        delay_max_seconds=0,
        headless=not args.headed,
        proxy_policy=args.proxy_policy,
        proxy_url=args.proxy_url,
        browser_channel="msedge",
        storage_state_path=(
            PROJECT_ROOT
            / ".crawler-state"
            / "recruitment"
            / "job51"
            / "storage_state.json"
        ),
    )
    crawler = Job51Crawler(config)
    results: list[dict[str, Any]] = []
    with sync_playwright() as playwright:
        for code in args.codes:
            probe = _probe_code if args.mode == "observe" else _probe_crawler_path
            results.append(probe(crawler, playwright, code))
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(item["outcome"] == "response" for item in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
