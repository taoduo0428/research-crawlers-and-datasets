from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import hmac
import json
import logging
import os
from pathlib import Path
import random
import re
import socket
import time
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import parse_qs, quote, urlencode, urlparse
from uuid import uuid4

from curl_cffi import requests as curl_requests

from .models import CategoryPath, CrawlConfig
from .state import CrawlState, atomic_write_json


LOGGER = logging.getLogger("job51")
BASE_URL = "https://we.51job.com"
SEARCH_PATH = "/pc/search"
SEARCH_API_FRAGMENT = "/api/job/search-pc"
CUPID_BASE_URL = "https://cupid.51job.com"
FRESH_SEARCH_PATH = "/open/noauth/jobs/fresh-job-list"
CUPID_SIGNING_KEY = "abfc8f9dcf8c3f3d8aa294ac5f2cf2cc7767e5592590f39c3f503271dd68562b"
MAX_FRESH_RESPONSE_BYTES = 5 * 1024 * 1024
AUTH_SMOKE_FUNCTION_CODE = "A0N7"
WORK_FUNCTION_LABEL = "\u5de5\u4f5c\u804c\u80fd"
SECRET_PATTERN = re.compile(
    r"(?i)(user-token|authorization|cookie|sign|token|uuid|decode__1048)=([^&\s]+)"
)


class Job51Error(RuntimeError):
    pass


class DependencyError(Job51Error):
    pass


class BrowserBootstrapError(Job51Error):
    pass


class UpstreamSchemaError(Job51Error):
    pass


class SessionRequiredError(Job51Error):
    pass


class HumanVerificationRequired(Job51Error):
    pass


class RateLimitedError(Job51Error):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class UpstreamHTTPError(Job51Error):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class TransportError(Job51Error):
    pass


@dataclass(slots=True)
class SearchPage:
    items: list[dict[str, Any]]
    total_count: int
    request_id: str | None


@dataclass(slots=True)
class CodeResult:
    records: list[dict[str, Any]]
    status: str
    next_page: int
    mismatch_count: int
    total_available: int | None


@dataclass(slots=True)
class RunSummary:
    output_path: Path
    coverage_path: Path
    state_db_path: Path
    category_paths: int
    unique_search_codes: int
    codes_attempted: int
    records_total: int
    coverage_rate: float
    quota_met_rate: float
    stop_reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_path": str(self.output_path),
            "coverage_path": str(self.coverage_path),
            "state_db_path": str(self.state_db_path),
            "category_paths": self.category_paths,
            "unique_search_codes": self.unique_search_codes,
            "codes_attempted": self.codes_attempted,
            "records_total": self.records_total,
            "coverage_rate": self.coverage_rate,
            "quota_met_rate": self.quota_met_rate,
            "stop_reason": self.stop_reason,
        }


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def redact_message(value: object) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    return SECRET_PATTERN.sub(lambda match: f"{match.group(1)}=<redacted>", text)[:1000]


def _node_code(node: Mapping[str, Any]) -> str:
    raw = node.get("id") or node.get("value")
    if raw is None:
        raise UpstreamSchemaError("category node is missing id/value")
    return re.sub(r"_[123]$", "", str(raw).strip())


def _node_label(node: Mapping[str, Any]) -> str:
    value = str(node.get("label") or "").strip()
    if not value:
        raise UpstreamSchemaError("category node is missing label")
    return value


def flatten_category_tree(tree: object) -> list[CategoryPath]:
    if not isinstance(tree, list) or not tree:
        raise UpstreamSchemaError("official function tree is empty or not a list")
    categories: list[CategoryPath] = []
    seen_paths: set[str] = set()
    ordinal = 0
    for level_1 in tree:
        if not isinstance(level_1, Mapping):
            raise UpstreamSchemaError("level-1 category is not an object")
        level_2_nodes = level_1.get("children")
        if not isinstance(level_2_nodes, list):
            raise UpstreamSchemaError("level-1 category has no child list")
        for level_2 in level_2_nodes:
            if not isinstance(level_2, Mapping):
                raise UpstreamSchemaError("level-2 category is not an object")
            level_3_nodes = level_2.get("children")
            if not isinstance(level_3_nodes, list):
                raise UpstreamSchemaError("level-2 category has no child list")
            for level_3 in level_3_nodes:
                if not isinstance(level_3, Mapping):
                    raise UpstreamSchemaError("level-3 category is not an object")
                ordinal += 1
                category = CategoryPath(
                    level_1_code=_node_code(level_1),
                    level_1_name=_node_label(level_1),
                    level_2_code=_node_code(level_2),
                    level_2_name=_node_label(level_2),
                    level_3_code=_node_code(level_3),
                    level_3_name=_node_label(level_3),
                    ordinal=ordinal,
                )
                if category.path_key in seen_paths:
                    raise UpstreamSchemaError(
                        f"duplicate official category path: {category.path_key}"
                    )
                seen_paths.add(category.path_key)
                categories.append(category)
    if not categories:
        raise UpstreamSchemaError("official function tree contains no leaf categories")
    return categories


def category_snapshot(categories: Sequence[CategoryPath]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "site": "51job",
        "source": "live work-function dialog",
        "source_url": f"{BASE_URL}{SEARCH_PATH}",
        "captured_at": utc_now_iso(),
        "level_1_count": len({item.level_1_code for item in categories}),
        "level_2_path_count": len(
            {(item.level_1_code, item.level_2_code) for item in categories}
        ),
        "level_3_path_count": len(categories),
        "unique_search_code_count": len({item.search_code for item in categories}),
        "categories": [item.to_dict() for item in categories],
    }


def load_category_snapshot(path: Path) -> list[CategoryPath]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        rows = value["categories"]
        if value.get("site") != "51job" or not isinstance(rows, list):
            raise ValueError("invalid snapshot identity")
        categories = [CategoryPath.from_dict(row) for row in rows]
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise UpstreamSchemaError(f"invalid category snapshot: {redact_message(exc)}") from exc
    if not categories:
        raise UpstreamSchemaError("category snapshot has no categories")
    return categories


def _proxy_reachable(proxy_url: str, timeout_seconds: float = 1.5) -> bool:
    parsed = urlparse(proxy_url)
    if parsed.scheme not in {"http", "https", "socks5"} or not parsed.hostname:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((parsed.hostname, port), timeout=timeout_seconds):
            return True
    except OSError:
        return False


def resolve_proxy_url(config: CrawlConfig) -> str | None:
    if config.proxy_policy == "off":
        return None
    candidate = (
        config.proxy_url
        or os.environ.get("CRAWLER_PROXY_URL")
        or "http://127.0.0.1:7897"
    )
    if _proxy_reachable(candidate):
        return candidate
    if config.proxy_policy == "required":
        parsed = urlparse(candidate)
        label = parsed.hostname or "invalid-proxy"
        if parsed.port:
            label = f"{label}:{parsed.port}"
        raise BrowserBootstrapError(f"required proxy is unavailable: {label}")
    LOGGER.info("proxy unavailable; falling back to direct connection")
    return None


def _as_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(",", "").strip())
    except ValueError:
        return None
    if number < 0:
        return None
    return int(round(number))


def _salary_period(raw_text: str, minimum: int | None, maximum: int | None) -> str | None:
    if minimum is None and maximum is None:
        return None
    lowered = raw_text.lower()
    if "/\u5e74" in raw_text or "\u5e74\u85aa" in raw_text or "/year" in lowered:
        return "year"
    if "/\u5929" in raw_text or "/day" in lowered:
        return "day"
    if "/\u65f6" in raw_text or "/hour" in lowered:
        return "hour"
    return "month"


def parse_salary_bounds(raw_text: str) -> tuple[int | None, int | None]:
    """Parse explicit CNY bounds without converting between salary periods."""
    text = raw_text.strip().lower().replace(",", "").replace("\uff0c", "")
    if not text or "\u9762\u8bae" in text:
        return None, None
    text = re.sub(r"[\u00b7.]?\d+\s*\u85aa.*$", "", text)

    def amount(number: str, unit: str) -> int:
        multiplier = 10_000 if unit == "\u4e07" else 1_000 if unit in {"\u5343", "k"} else 1
        return int(round(float(number) * multiplier))

    range_match = re.search(
        r"(\d+(?:\.\d+)?)\s*([\u4e07\u5343k]?)\s*[-~\uff5e\u2014\u81f3]\s*"
        r"(\d+(?:\.\d+)?)\s*([\u4e07\u5343k]?)",
        text,
    )
    if range_match:
        low_number, low_unit, high_number, high_unit = range_match.groups()
        low_unit = low_unit or high_unit
        high_unit = high_unit or low_unit
        return amount(low_number, low_unit), amount(high_number, high_unit)
    single_match = re.search(r"(\d+(?:\.\d+)?)\s*([\u4e07\u5343k]?)", text)
    if not single_match:
        return None, None
    value = amount(*single_match.groups())
    if "\u4ee5\u4e0b" in text or "\u4ee5\u5185" in text:
        return None, value
    if "\u4ee5\u4e0a" in text or "\u8d77" in text:
        return value, None
    return value, value


def _function_codes(item: Mapping[str, Any]) -> set[str]:
    output: set[str] = set()
    for key in ("funcType1Code", "funcType2Code"):
        value = item.get(key)
        if isinstance(value, list):
            output.update(str(part).strip() for part in value if str(part).strip())
        elif value:
            output.update(
                part.strip() for part in re.split(r"[,|]", str(value)) if part.strip()
            )
    return output


def matches_function_code(item: Mapping[str, Any], function_code: str) -> bool:
    return function_code in _function_codes(item)


def has_nonzero_numeric_salary(record: Mapping[str, Any]) -> bool:
    metadata = record.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    for key in ("salary_min", "salary_max"):
        value = metadata.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return True
    return False


def normalize_job(
    item: Mapping[str, Any],
    function_code: str,
    city_code: str,
    *,
    page_no: int = 1,
    list_position: int = 1,
) -> dict[str, Any]:
    job_id = str(item.get("jobId") or "").strip()
    detail_url = str(item.get("jobHref") or "").strip()
    title = str(item.get("jobName") or "").strip()
    company = str(item.get("companyName") or "").strip()
    if not title:
        raise UpstreamSchemaError("job item is missing jobName")
    if not job_id:
        identity = "|".join((detail_url, title, company, function_code))
        job_id = sha256(identity.encode("utf-8")).hexdigest()[:24]
    salary_text = str(item.get("provideSalaryString") or "").strip()
    salary_min = _as_int(item.get("jobSalaryMin"))
    salary_max = _as_int(item.get("jobSalaryMax"))
    salary_min = salary_min if salary_min else None
    salary_max = salary_max if salary_max else None
    parsed_min, parsed_max = parse_salary_bounds(salary_text)
    salary_min = salary_min if salary_min is not None else parsed_min
    salary_max = salary_max if salary_max is not None else parsed_max
    area = item.get("jobAreaLevelDetail")
    area = area if isinstance(area, Mapping) else {}
    is_internship = bool(item.get("isIntern")) or str(item.get("term")) == "4"
    record_id = f"51job-{job_id}"
    source_query = urlencode({"jobArea": city_code, "function": function_code})
    source_url = f"{BASE_URL}{SEARCH_PATH}?{source_query}"
    period = _salary_period(salary_text, salary_min, salary_max)
    if period == "year":
        price_type = "annual_salary"
    elif period == "day":
        price_type = "daily_salary"
    elif period == "month":
        price_type = "monthly_salary"
    elif salary_text:
        price_type = "negotiable" if "\u9762\u8bae" in salary_text else "unknown"
    else:
        price_type = "unknown"
    price_evidence = []
    if salary_text:
        price_evidence.append(
            {
                "raw_text": salary_text,
                "price_type": price_type,
                "price_source": "api_response",
                "evidence_url": detail_url or source_url,
                "evidence_location": "resultbody.job.items[].provideSalaryString",
                "package_name": None,
                "currency_text": "CNY" if salary_min is not None or salary_max is not None else None,
                "period_text": period,
                "negotiable": "\u9762\u8bae" in salary_text,
                "estimated_by_platform": False,
                "captured_at": utc_now_iso(),
            }
        )
    category_verified = matches_function_code(item, function_code)
    record = {
        "record_id": record_id,
        "site_name": "51job",
        "content_category": "internship" if is_internship else "full_time_job",
        "title_raw": title,
        "list_url": source_url,
        "detail_url": detail_url or None,
        "page_no": page_no,
        "list_position": list_position,
        "price_evidence": price_evidence,
        "price_raw_joined": salary_text,
        "core_complete": bool(title and price_evidence),
        "detail_title_raw": None,
        "category_level_1_raw": None,
        "category_level_2_raw": None,
        "category_level_3_raw": None,
        "category_path_raw": None,
        "category_source": "official_work_function_tree",
        "category_source_url": source_url,
        "category_identity_verified": category_verified,
        "status_raw": "expired" if item.get("isExpire") else "active",
        "job_type_raw": item.get("termStr") or item.get("jobType") or None,
        "budget_raw": salary_text or None,
        "work_period_raw": None,
        "description_raw": item.get("jobDescribe") or None,
        "publisher_name_raw": company or None,
        "location_raw": item.get("jobAreaString") or None,
        "published_at_raw": item.get("issueDateString") or None,
        "capture_method": "playwright_signed_api_response",
        "source_state": "expired" if item.get("isExpire") else "active",
        "captured_at": utc_now_iso(),
        "metadata": {
            "job_id": job_id,
            "company_name": company or None,
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_period": period,
            "salary_currency": "CNY" if salary_min is not None or salary_max is not None else None,
            "city": area.get("cityString") or item.get("jobAreaString"),
            "district": area.get("districtString") or None,
            "experience": item.get("workYearString") or None,
            "education": item.get("degreeString") or None,
            "company_size": item.get("companySizeString") or None,
            "company_type": item.get("companyTypeString") or None,
            "industry": item.get("companyIndustryType1Str") or item.get("industryType1Str") or None,
            "update_time": item.get("updateDateTime") or None,
            "source_function_code": function_code,
            "source_function_codes_on_item": sorted(_function_codes(item)),
            "verified_request_city_code": city_code,
            "raw_item": dict(item),
        },
    }
    record["metadata"]["quota_salary_eligible"] = has_nonzero_numeric_salary(
        record
    )
    return record


def parse_search_payload(payload: object) -> SearchPage:
    if not isinstance(payload, Mapping):
        raise UpstreamSchemaError("search response is not an object")
    status = str(payload.get("status") or "")
    message = str(payload.get("message") or "")
    if status not in {"1", "200"}:
        if any(word in message for word in ("\u767b\u5f55", "\u767b\u9304", "login")):
            raise SessionRequiredError(message or f"search status={status}")
        if any(word in message for word in ("\u9891\u7e41", "\u9650\u6d41", "rate")):
            raise RateLimitedError(message or f"search status={status}")
        raise UpstreamSchemaError(message or f"unexpected search status={status}")
    result_body = payload.get("resultbody")
    if not isinstance(result_body, Mapping):
        raise UpstreamSchemaError("search response is missing resultbody")
    job = result_body.get("job")
    if not isinstance(job, Mapping):
        raise UpstreamSchemaError("search response is missing resultbody.job")
    items = job.get("items")
    if not isinstance(items, list):
        raise UpstreamSchemaError("search response job.items is not a list")
    normalized_items: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise UpstreamSchemaError("search response contains a non-object job item")
        normalized_items.append(dict(item))
    total = _as_int(job.get("totalcount"))
    if total is None:
        total = _as_int(job.get("totalCount"))
    if total is None:
        raise UpstreamSchemaError("search response is missing numeric totalcount")
    request_id = result_body.get("requestId")
    return SearchPage(
        items=normalized_items,
        total_count=total,
        request_id=None if request_id is None else str(request_id),
    )


def _login_probe_is_authenticated(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    raw_is_login = value.get("isLogin")
    if isinstance(raw_is_login, str):
        is_login = raw_is_login.strip().lower() == "true"
    else:
        is_login = raw_is_login is True or raw_is_login == 1
    account_id = str(value.get("accountId") or "").strip()
    return is_login and bool(account_id)


def _probe_page_login(page: Any) -> bool:
    value = page.evaluate(
        """
        () => {
          for (const element of document.querySelectorAll('*')) {
            const component = element.__vue__;
            if (!component || !component.$options ||
                component.$options.name !== 'PCSearch') continue;
            const commonStore = component.$store && component.$store.state &&
              component.$store.state.commonStore;
            return {
              isLogin: component.isLogin === true,
              accountId: commonStore && commonStore.accountId
                ? String(commonStore.accountId).trim()
                : ''
            };
          }
          return null;
        }
        """
    )
    return _login_probe_is_authenticated(value)


class Job51Crawler:
    """Discover the live official taxonomy and sample every leaf code."""

    def __init__(self, config: CrawlConfig, http_session: Any | None = None) -> None:
        self.config = config
        self._runtime_session_context: Any | None = None
        self._http_session = http_session
        self._owns_http_session = http_session is None
        self._anonymous_uuid = f"{uuid4().int % (10**20):020d}"

    def _get_http_session(self) -> Any:
        if self._http_session is not None:
            return self._http_session
        proxy_url = resolve_proxy_url(self.config)
        session_args: dict[str, Any] = {"impersonate": "chrome"}
        if proxy_url:
            session_args["proxies"] = {"http": proxy_url, "https": proxy_url}
        session = curl_requests.Session(**session_args)
        self._http_session = session
        return session

    def _close_http_session(self) -> None:
        if self._owns_http_session and self._http_session is not None:
            self._http_session.close()
        self._http_session = None

    @staticmethod
    def _retry_after(headers: Mapping[str, Any]) -> float | None:
        raw = str(headers.get("retry-after") or "").strip()
        try:
            value = float(raw)
        except ValueError:
            return None
        return value if value > 0 else None

    def _fresh_search_once(
        self,
        function_code: str,
        page_no: int,
        request_id: str = "",
    ) -> SearchPage:
        timestamp = int(time.time())
        query = urlencode(
            [
                ("api_key", "51job"),
                ("timestamp", str(timestamp)),
                ("function", function_code),
                ("jobArea", self.config.city_code),
                ("pageNum", str(page_no)),
                ("pageSize", str(self.config.page_size)),
                ("requestId", request_id),
            ]
        )
        relative_url = f"{FRESH_SEARCH_PATH}?{query}"
        signature = hmac.new(
            CUPID_SIGNING_KEY.encode("ascii"),
            relative_url.encode("utf-8"),
            sha256,
        ).hexdigest()
        property_value = {
            "partner": "",
            "webId": 2,
            "fromdomain": "51job_web",
            "frompageUrl": "",
            "pageUrl": "https://www.51job.com/",
            "isLogin": "\u5426",
            "accountId": "",
        }
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/150.0.0.0 Safari/537.36"
            ),
            "From-Domain": "51job_web",
            "Referer": "https://www.51job.com/",
            "account-id": "",
            "user-token": "",
            "partner": "",
            "property": quote(
                json.dumps(
                    property_value,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                safe="",
            ),
            "sign": signature,
            "uuid": self._anonymous_uuid,
        }
        timeout_seconds = self.config.timeout_ms / 1000.0
        timeout = (min(10.0, timeout_seconds), timeout_seconds)
        try:
            response = self._get_http_session().get(
                f"{CUPID_BASE_URL}{relative_url}",
                headers=headers,
                timeout=timeout,
            )
        except curl_requests.RequestsError as exc:
            raise TransportError(
                f"fresh job API request failed: {redact_message(exc)}"
            ) from exc

        status = int(response.status_code)
        content_type = str(response.headers.get("content-type") or "").lower()
        if status in {401, 403}:
            raise SessionRequiredError(f"fresh job API returned HTTP {status}")
        if status in {418, 429}:
            raise RateLimitedError(
                f"fresh job API returned HTTP {status}",
                self._retry_after(response.headers),
            )
        if status >= 500:
            raise UpstreamHTTPError(
                status, f"fresh job API returned HTTP {status}"
            )
        if status != 200:
            raise UpstreamHTTPError(
                status, f"fresh job API returned HTTP {status}"
            )
        content_length = _as_int(response.headers.get("content-length"))
        if (
            content_length is not None
            and content_length > MAX_FRESH_RESPONSE_BYTES
        ):
            raise UpstreamSchemaError(
                "fresh job API response exceeds the 5 MiB limit"
            )
        body = response.content
        if isinstance(body, (bytes, bytearray)) and len(body) > MAX_FRESH_RESPONSE_BYTES:
            raise UpstreamSchemaError(
                "fresh job API response exceeds the 5 MiB limit"
            )
        if "text/html" in content_type:
            raise RateLimitedError(
                "fresh job API returned HTML instead of JSON",
                self.config.soft_block_cooldown_seconds,
            )
        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise UpstreamSchemaError(
                "fresh job API returned invalid JSON"
            ) from exc
        return parse_search_payload(payload)

    def _storage_state_candidates(
        self,
        *,
        load_storage_state: bool,
        prefer_runtime_session: bool,
    ) -> list[tuple[str, Path | None]]:
        candidates: list[tuple[str, Path | None]] = []
        if load_storage_state:
            if (
                prefer_runtime_session
                and self.config.runtime_session_state_path.exists()
            ):
                candidates.append(("runtime", self.config.runtime_session_state_path))
            if self.config.storage_state_path.exists():
                candidates.append(("login", self.config.storage_state_path))
        candidates.append(("empty", None))
        return candidates

    @staticmethod
    def _read_storage_state(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise BrowserBootstrapError(
                f"could not load browser storage state: {redact_message(exc)}"
            ) from exc
        if not isinstance(value, dict):
            raise BrowserBootstrapError("browser storage state is not an object")
        cookies = value.get("cookies")
        origins = value.get("origins")
        if not isinstance(cookies, list) or not isinstance(origins, list):
            raise BrowserBootstrapError(
                "browser storage state is missing cookies/origins lists"
            )
        return value

    def _select_storage_state(
        self,
        *,
        load_storage_state: bool,
        prefer_runtime_session: bool,
    ) -> dict[str, Any] | None:
        for state_kind, state_path in self._storage_state_candidates(
            load_storage_state=load_storage_state,
            prefer_runtime_session=prefer_runtime_session,
        ):
            if state_path is None:
                return None
            try:
                return self._read_storage_state(state_path)
            except BrowserBootstrapError as exc:
                if state_kind == "runtime":
                    LOGGER.warning(
                        "could not load runtime browser session; falling back: %s",
                        redact_message(exc),
                    )
                    continue
                raise
        return None

    @staticmethod
    def _inject_storage_state(context: Any, storage_state: Mapping[str, Any]) -> None:
        cookies = storage_state.get("cookies")
        if cookies:
            context.add_cookies(cookies)
        origin_storage: dict[str, list[dict[str, str]]] = {}
        for origin_entry in storage_state.get("origins", []):
            if not isinstance(origin_entry, Mapping):
                continue
            origin = str(origin_entry.get("origin") or "").strip()
            local_storage = origin_entry.get("localStorage")
            if not origin or not isinstance(local_storage, list):
                continue
            valid_items: list[dict[str, str]] = []
            for item in local_storage:
                if not isinstance(item, Mapping) or "name" not in item:
                    continue
                valid_items.append(
                    {
                        "name": str(item["name"]),
                        "value": str(item.get("value") or ""),
                    }
                )
            if valid_items:
                origin_storage[origin] = valid_items
        if not origin_storage:
            return
        serialized = json.dumps(origin_storage, ensure_ascii=True, separators=(",", ":"))
        context.add_init_script(
            """
            (() => {
              const originStorage = %s;
              const items = originStorage[location.origin];
              if (!items) return;
              for (const item of items) {
                localStorage.setItem(item.name, item.value);
              }
            })();
            """
            % serialized
        )

    def _launch_persistent_context(
        self,
        playwright: Any,
        *,
        load_storage_state: bool,
        prefer_runtime_session: bool,
    ) -> tuple[None, Any, Any]:
        profile_dir = self.config.persistent_profile_dir
        if profile_dir is None:
            raise BrowserBootstrapError("persistent browser profile is not configured")
        try:
            profile_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise BrowserBootstrapError(
                f"could not prepare persistent browser profile: {redact_message(exc)}"
            ) from exc

        storage_state = self._select_storage_state(
            load_storage_state=load_storage_state,
            prefer_runtime_session=prefer_runtime_session,
        )

        launch_args: dict[str, Any] = {
            "headless": self.config.headless,
            "locale": "zh-CN",
        }
        proxy_url = resolve_proxy_url(self.config)
        if proxy_url:
            launch_args["proxy"] = {"server": proxy_url}
        if self.config.browser_channel:
            launch_args["channel"] = self.config.browser_channel
        try:
            context = playwright.chromium.launch_persistent_context(
                str(profile_dir), **launch_args
            )
        except Exception as exc:
            raise BrowserBootstrapError(
                f"could not launch persistent browser profile: {redact_message(exc)}"
            ) from exc

        try:
            if storage_state is not None:
                self._inject_storage_state(context, storage_state)
            page = context.new_page()
            return None, context, page
        except Exception as exc:
            context.close()
            if isinstance(exc, BrowserBootstrapError):
                raise
            raise BrowserBootstrapError(
                f"could not initialize persistent browser profile: {redact_message(exc)}"
            ) from exc

    def _launch(
        self,
        playwright: Any,
        *,
        load_storage_state: bool = True,
        prefer_runtime_session: bool = True,
        use_persistent_profile: bool = True,
    ) -> tuple[Any, Any, Any]:
        if use_persistent_profile and self.config.persistent_profile_dir is not None:
            return self._launch_persistent_context(
                playwright,
                load_storage_state=load_storage_state,
                prefer_runtime_session=prefer_runtime_session,
            )
        proxy_url = resolve_proxy_url(self.config)
        launch_args: dict[str, Any] = {"headless": self.config.headless}
        if proxy_url:
            launch_args["proxy"] = {"server": proxy_url}
        if self.config.browser_channel:
            launch_args["channel"] = self.config.browser_channel
        browser = playwright.chromium.launch(**launch_args)
        for state_kind, state_path in self._storage_state_candidates(
            load_storage_state=load_storage_state,
            prefer_runtime_session=prefer_runtime_session,
        ):
            context_args: dict[str, Any] = {"locale": "zh-CN"}
            if state_path is not None:
                context_args["storage_state"] = str(state_path)
            try:
                context = browser.new_context(**context_args)
                break
            except Exception as exc:
                if state_kind == "runtime":
                    LOGGER.warning(
                        "could not load runtime browser session; falling back: %s",
                        redact_message(exc),
                    )
                    continue
                browser.close()
                raise BrowserBootstrapError(
                    f"could not load browser storage state: {redact_message(exc)}"
                ) from exc
        page = context.new_page()
        return browser, context, page

    def _persist_runtime_session_state(self) -> bool:
        """Persist only a session proven by a parsed search response."""
        context = self._runtime_session_context
        if context is None:
            return False
        try:
            storage_state = context.storage_state()
            if not isinstance(storage_state, Mapping):
                raise UpstreamSchemaError("browser storage state is not an object")
            if not isinstance(storage_state.get("cookies"), list) or not isinstance(
                storage_state.get("origins"), list
            ):
                raise UpstreamSchemaError(
                    "browser storage state is missing cookies/origins lists"
                )
            atomic_write_json(
                self.config.runtime_session_state_path, dict(storage_state)
            )
            return True
        except Exception as exc:
            # Search data remains valid even when a local session checkpoint cannot be saved.
            LOGGER.warning(
                "could not persist runtime browser session; crawl will continue: %s",
                redact_message(exc),
            )
            return False

    def _bootstrap_page(self, page: Any) -> None:
        query = urlencode({"jobArea": self.config.city_code})
        try:
            response = page.goto(
                f"{BASE_URL}{SEARCH_PATH}?{query}",
                wait_until="domcontentloaded",
                timeout=self.config.timeout_ms,
            )
            status = int(response.status) if response is not None else None
            html = page.content()[:50_000].lower()
            block_markers = (
                "_waf_bd8ce2ce37",
                "aliyun_waf_",
                'id="renderdata"',
                "\u8bbf\u95ee\u88ab\u963b\u65ad",
                "\u5b89\u5168\u5a01\u80c1",
            )
            if status in {403, 405, 418, 429} or any(
                marker in html for marker in block_markers
            ):
                retry_value = (
                    response.headers.get("retry-after") if response is not None else None
                )
                retry_after = (
                    float(retry_value)
                    if retry_value and str(retry_value).isdigit()
                    else self.config.soft_block_cooldown_seconds
                )
                raise RateLimitedError(
                    f"51job search page was blocked with HTTP {status}", retry_after
                )
            page.wait_for_function(
                """
                () => Array.from(document.querySelectorAll('*')).some(
                  element => element.__vue__ && element.__vue__.$options &&
                    element.__vue__.$options.name === 'PCSearch'
                )
                """,
                timeout=self.config.timeout_ms,
            )
        except RateLimitedError:
            raise
        except Exception as exc:
            raise BrowserBootstrapError(
                f"51job search application did not initialize: {redact_message(exc)}"
            ) from exc

    def discover_categories(self, page: Any) -> list[CategoryPath]:
        try:
            page.get_by_text(WORK_FUNCTION_LABEL, exact=True).click(
                timeout=self.config.timeout_ms
            )
            page.wait_for_selector(
                ".functype_cascader_dialog .jbs_cascader_panel",
                state="visible",
                timeout=self.config.timeout_ms,
            )
            page.wait_for_function(
                """
                () => Array.from(document.querySelectorAll('.jbs_cascader_panel')).some(
                  element => {
                    const rect = element.getBoundingClientRect();
                    const component = element.__vue__;
                    const options = component && component.$props && component.$props.options;
                    return rect.width > 0 && rect.height > 0 &&
                      Array.isArray(options) && options.length > 0;
                  }
                )
                """,
                timeout=self.config.timeout_ms,
            )
            tree = page.evaluate(
                """
                () => {
                  const panels = Array.from(document.querySelectorAll('.jbs_cascader_panel'));
                  const panel = panels.find(element => {
                    const rect = element.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                  });
                  if (panel && panel.__vue__ && panel.__vue__.$props.options) {
                    return JSON.parse(JSON.stringify(panel.__vue__.$props.options));
                  }
                  for (const element of document.querySelectorAll('*')) {
                    const component = element.__vue__;
                    if (component && component.$options &&
                        component.$options.name === 'JbsDictFuntypePopup') {
                      const options = component.originDict || component.options;
                      if (options) return JSON.parse(JSON.stringify(options));
                    }
                  }
                  return null;
                }
                """
            )
            categories = flatten_category_tree(tree)
            page.keyboard.press("Escape")
            return categories
        except Job51Error:
            raise
        except Exception as exc:
            raise UpstreamSchemaError(
                f"could not extract official function tree: {redact_message(exc)}"
            ) from exc

    def discover_and_save_categories(self, page: Any) -> list[CategoryPath]:
        try:
            categories = self.discover_categories(page)
        except Exception:
            if not self.config.category_snapshot_path.exists():
                raise
            LOGGER.warning(
                "live category discovery failed; using snapshot %s",
                self.config.category_snapshot_path,
            )
            return load_category_snapshot(self.config.category_snapshot_path)
        atomic_write_json(
            self.config.category_snapshot_path, category_snapshot(categories)
        )
        return categories

    @staticmethod
    def _search_response_matches(
        response: Any, function_code: str, city_code: str, page_no: int
    ) -> bool:
        if SEARCH_API_FRAGMENT not in response.url:
            return False
        query = parse_qs(urlparse(response.url).query)
        return (
            query.get("function", [""])[0] == function_code
            and query.get("jobArea", [""])[0] == city_code
            and query.get("pageNum", [""])[0] == str(page_no)
        )

    def _search_once(self, page: Any, function_code: str, page_no: int) -> SearchPage:
        try:
            with page.expect_response(
                lambda response: self._search_response_matches(
                    response, function_code, self.config.city_code, page_no
                ),
                timeout=self.config.timeout_ms,
            ) as response_info:
                page.evaluate(
                    """
                    async ({functionCode, cityCode, pageNumber, requestTimeoutMs}) => {
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
                      search.searchParams.pageNum = pageNumber;
                      if (pageNumber === 1) search.searchParams.requestId = '';
                      await new Promise((resolve, reject) => {
                        const timer = setTimeout(
                          () => reject(new Error('PCSearch.getSearch timed out')),
                          requestTimeoutMs
                        );
                        Promise.resolve(
                          search.getSearch(pageNumber === 1 ? '' : pageNumber)
                        ).then(
                          value => {
                            clearTimeout(timer);
                            resolve(value);
                          },
                          error => {
                            clearTimeout(timer);
                            reject(error);
                          }
                        );
                      });
                    }
                    """,
                    {
                        "functionCode": function_code,
                        "cityCode": self.config.city_code,
                        "pageNumber": page_no,
                        "requestTimeoutMs": max(1_000, self.config.timeout_ms - 1_000),
                    },
                )
            response = response_info.value
            content_type = str(response.headers.get("content-type") or "").lower()
            if "text/html" in content_type:
                body_prefix = response.text()[:4_096].lower()
                if any(
                    marker in body_prefix
                    for marker in ("_waf_bd8ce2ce37", "aliyun_waf_", "id=\"renderdata\"")
                ):
                    raise RateLimitedError(
                        "search API returned an Aliyun WAF HTML response",
                        retry_after=self.config.soft_block_cooldown_seconds,
                    )
                raise UpstreamSchemaError(
                    "search API returned HTML instead of the expected JSON payload"
                )
            if response.status in {401, 403}:
                raise SessionRequiredError(
                    f"search API returned HTTP {response.status}"
                )
            if response.status == 429:
                retry_value = response.headers.get("retry-after")
                retry_after = float(retry_value) if retry_value and retry_value.isdigit() else None
                raise RateLimitedError("search API returned HTTP 429", retry_after)
            if response.status >= 500:
                raise UpstreamHTTPError(
                    response.status, f"search API returned HTTP {response.status}"
                )
            if response.status != 200:
                raise UpstreamHTTPError(
                    response.status, f"search API returned HTTP {response.status}"
                )
            result = parse_search_payload(response.json())
            self._persist_runtime_session_state()
            return result
        except Job51Error:
            raise
        except Exception as exc:
            message = redact_message(exc)
            if self._verification_visible(page):
                raise HumanVerificationRequired(
                    "51job requires manual browser verification"
                ) from exc
            raise BrowserBootstrapError(f"search request failed: {message}") from exc

    @staticmethod
    def _verification_visible(page: Any) -> bool:
        try:
            text = page.locator("body").inner_text(timeout=2_000)[:8_000]
        except Exception:
            return False
        markers = (
            "\u6ed1\u5757\u9a8c\u8bc1",
            "\u4eba\u673a\u9a8c\u8bc1",
            "\u8bf7\u5b8c\u6210\u9a8c\u8bc1",
            "\u73af\u5883\u5b58\u5728\u5f02\u5e38",
        )
        return any(marker in text for marker in markers)

    def _search_with_retry(
        self,
        page: Any | None,
        function_code: str,
        page_no: int,
        *,
        request_id: str = "",
        prior_total: int | None = None,
    ) -> SearchPage:
        last_error: Exception | None = None
        for attempt in range(self.config.request_retries + 1):
            try:
                if page is None:
                    result = self._fresh_search_once(
                        function_code, page_no, request_id
                    )
                    if result.items and result.total_count < len(result.items):
                        raise TransportError(
                            "fresh job API returned an inconsistent total count"
                        )
                    if (
                        prior_total is not None
                        and prior_total > (page_no - 1) * self.config.page_size
                        and not result.items
                        and result.total_count == 0
                    ):
                        raise TransportError(
                            "fresh job API returned an inconsistent empty page"
                        )
                    return result
                return self._search_once(page, function_code, page_no)
            except (SessionRequiredError, HumanVerificationRequired, UpstreamSchemaError):
                raise
            except RateLimitedError as exc:
                last_error = exc
                # 全局限流/WAF 不在同一浏览器会话内重试；持久冷却后由下次批次续跑。
                break
            except (TransportError, BrowserBootstrapError, UpstreamHTTPError) as exc:
                last_error = exc
                if isinstance(exc, UpstreamHTTPError) and exc.status < 500:
                    raise
                if isinstance(exc, BrowserBootstrapError) and page is not None:
                    try:
                        self._bootstrap_page(page)
                    except BrowserBootstrapError as recovery_error:
                        last_error = recovery_error
                if attempt >= self.config.request_retries:
                    break
                time.sleep(min(8.0, 2.0 ** attempt) + random.uniform(0.0, 0.5))
        assert last_error is not None
        raise last_error

    def _collect_code(
        self,
        page: Any | None,
        state: CrawlState,
        function_code: str,
        start_page: int,
    ) -> CodeResult:
        existing_record_ids = state.code_record_ids(function_code)
        existing_count = len(existing_record_ids)
        if existing_count >= self.config.per_category_limit:
            return CodeResult([], "completed", start_page, 0, None)
        records: dict[str, dict[str, Any]] = {}
        rejected_record_ids: set[str] = set()
        mismatch_count = 0
        total_available: int | None = None
        next_page = max(1, start_page)
        pages_used = 0
        exhausted = False
        request_id = ""
        while pages_used < self.config.max_pages_per_category:
            if page is None:
                result = self._search_with_retry(
                    None,
                    function_code,
                    next_page,
                    request_id=request_id,
                    prior_total=total_available,
                )
                request_id = result.request_id or request_id
            else:
                result = self._search_with_retry(page, function_code, next_page)
            pages_used += 1
            total_available = max(total_available or 0, result.total_count)
            for list_position, item in enumerate(result.items, start=1):
                if not matches_function_code(item, function_code):
                    mismatch_count += 1
                    continue
                record = normalize_job(
                    item,
                    function_code,
                    self.config.city_code,
                    page_no=next_page,
                    list_position=list_position,
                )
                if page is None:
                    record["capture_method"] = "signed_anonymous_api_response"
                    record["metadata"]["search_transport"] = (
                        "cupid_fresh_job_list"
                    )
                record_id = str(record["record_id"])
                if (
                    record_id in existing_record_ids
                    or record_id in records
                    or record_id in rejected_record_ids
                ):
                    continue
                if (
                    self.config.numeric_salary_only
                    and not has_nonzero_numeric_salary(record)
                ):
                    rejected_record_ids.add(record_id)
                    continue
                records[record_id] = record
                if existing_count + len(records) >= self.config.per_category_limit:
                    break
            if existing_count + len(records) >= self.config.per_category_limit:
                exhausted = True
                break
            if not result.items or next_page * self.config.page_size >= result.total_count:
                exhausted = True
                break
            next_page += 1
            if pages_used < self.config.max_pages_per_category:
                self._sleep_between_pages()
        if total_available and not records and not existing_count and mismatch_count:
            raise UpstreamSchemaError(
                f"all returned jobs mismatched function code {function_code}"
            )
        kept = list(records.values())[
            : max(0, self.config.per_category_limit - existing_count)
        ]
        total_kept = existing_count + len(kept)
        if total_kept == 0 and total_available == 0:
            status = "empty"
        elif total_kept >= self.config.per_category_limit or exhausted:
            status = "completed"
        else:
            status = "partial"
        resume_page = next_page
        return CodeResult(
            kept,
            status,
            resume_page,
            mismatch_count,
            total_available,
        )

    def _sleep_between_pages(self) -> None:
        if self.config.delay_max_seconds:
            time.sleep(
                random.uniform(
                    self.config.delay_min_seconds,
                    self.config.delay_max_seconds,
                )
            )

    def _record_code_error(
        self,
        state: CrawlState,
        function_code: str,
        exc: Exception,
        next_page: int,
    ) -> str | None:
        error_type = type(exc).__name__
        message = redact_message(exc)
        if isinstance(exc, RateLimitedError):
            state.defer_code(function_code, error_type, message, next_page)
            cooldown_seconds = (
                exc.retry_after
                if exc.retry_after is not None and exc.retry_after > 0
                else self.config.soft_block_cooldown_seconds
            )
            state.set_rate_limit_cooldown(
                cooldown_seconds,
                f"{error_type}: {message}",
            )
            LOGGER.error(
                "function code %s deferred for %.1fs: %s: %s",
                function_code,
                cooldown_seconds,
                error_type,
                message,
            )
            return "rate_limit_cooldown_required"
        state.fail_code(function_code, error_type, message, next_page)
        LOGGER.error(
            "function code %s failed: %s: %s",
            function_code,
            error_type,
            message,
        )
        if isinstance(exc, (SessionRequiredError, HumanVerificationRequired)):
            return error_type
        return None

    def _sleep_after_code(self, codes_processed: int, *, has_more: bool) -> None:
        if not has_more:
            return
        if (
            codes_processed % self.config.batch_size == 0
            and self.config.batch_cooldown_seconds
        ):
            LOGGER.info(
                "completed a batch of %d codes; cooling down for %.1f seconds",
                self.config.batch_size,
                self.config.batch_cooldown_seconds,
            )
            time.sleep(self.config.batch_cooldown_seconds)
            return
        if self.config.delay_max_seconds:
            time.sleep(
                random.uniform(
                    self.config.delay_min_seconds,
                    self.config.delay_max_seconds,
                )
            )

    def _run_category_queue(
        self,
        state: CrawlState,
        categories: Sequence[CategoryPath],
        page: Any | None,
    ) -> tuple[int, str]:
        state.prepare_run(self.config.run_scope(), categories)
        search_codes = state.pending_search_codes(self.config.max_category_attempts)
        stop_reason = "work_queue_exhausted"
        if self.config.max_codes is not None:
            search_codes = search_codes[: self.config.max_codes]
            stop_reason = "max_codes_reached"

        codes_attempted = 0
        consecutive_failures = 0
        for code_index, function_code in enumerate(search_codes, start=1):
            next_page, _attempts = state.begin_code(function_code)
            codes_attempted += 1
            try:
                result = self._collect_code(page, state, function_code, next_page)
                state.finish_code(
                    function_code,
                    result.records,
                    status=result.status,
                    next_page=result.next_page,
                    mismatch_count=result.mismatch_count,
                    total_available=result.total_available,
                )
                consecutive_failures = 0
            except Exception as exc:
                failure_stop = self._record_code_error(
                    state, function_code, exc, next_page
                )
                if failure_stop:
                    stop_reason = failure_stop
                    break
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    stop_reason = "consecutive_failure_budget_exhausted"
                    break
            self._sleep_after_code(
                code_index, has_more=code_index < len(search_codes)
            )
        return codes_attempted, stop_reason

    def run(self) -> RunSummary:
        codes_attempted = 0
        stop_reason = "work_queue_exhausted"
        with CrawlState(self.config.state_db_path) as state:
            cooldown = state.active_rate_limit_cooldown()
            if cooldown:
                LOGGER.warning(
                    "51job cooldown is active for another %.1f seconds; browser launch skipped",
                    cooldown["remaining_seconds"],
                )
                coverage = state.export(
                    self.config.output_path, self.config.coverage_path
                )
                return RunSummary(
                    output_path=self.config.output_path,
                    coverage_path=self.config.coverage_path,
                    state_db_path=self.config.state_db_path,
                    category_paths=int(coverage["official_category_paths"]),
                    unique_search_codes=int(coverage["unique_search_codes"]),
                    codes_attempted=0,
                    records_total=int(coverage["record_count"]),
                    coverage_rate=float(coverage["coverage_rate"]),
                    quota_met_rate=float(coverage["quota_met_rate"]),
                    stop_reason="rate_limit_cooldown_active",
                )
            try:
                if self.config.category_snapshot_path.exists():
                    categories = load_category_snapshot(
                        self.config.category_snapshot_path
                    )
                    codes_attempted, stop_reason = self._run_category_queue(
                        state, categories, None
                    )
                else:
                    try:
                        from playwright.sync_api import sync_playwright
                    except ImportError as exc:
                        raise DependencyError(
                            "playwright is required to discover categories; "
                            "install requirements and Chromium"
                        ) from exc
                    browser = context = None
                    with sync_playwright() as playwright:
                        try:
                            browser, context, page = self._launch(playwright)
                            self._runtime_session_context = context
                            try:
                                self._bootstrap_page(page)
                            except RateLimitedError as exc:
                                cooldown_seconds = (
                                    exc.retry_after
                                    if exc.retry_after is not None
                                    and exc.retry_after > 0
                                    else self.config.soft_block_cooldown_seconds
                                )
                                state.set_rate_limit_cooldown(
                                    cooldown_seconds,
                                    f"RateLimitedError: {redact_message(exc)}",
                                )
                                state.export(
                                    self.config.output_path,
                                    self.config.coverage_path,
                                )
                                raise
                            categories = self.discover_and_save_categories(page)
                            codes_attempted, stop_reason = (
                                self._run_category_queue(
                                    state, categories, page
                                )
                            )
                        finally:
                            self._runtime_session_context = None
                            if context is not None:
                                context.close()
                            if browser is not None:
                                browser.close()
                coverage = state.export(
                    self.config.output_path, self.config.coverage_path
                )
            finally:
                self._close_http_session()
        return RunSummary(
            output_path=self.config.output_path,
            coverage_path=self.config.coverage_path,
            state_db_path=self.config.state_db_path,
            category_paths=int(coverage["official_category_paths"]),
            unique_search_codes=int(coverage["unique_search_codes"]),
            codes_attempted=codes_attempted,
            records_total=int(coverage["record_count"]),
            coverage_rate=float(coverage["coverage_rate"]),
            quota_met_rate=float(coverage["quota_met_rate"]),
            stop_reason=stop_reason,
        )

    def export_categories(self, output_path: Path) -> dict[str, Any]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise DependencyError("playwright is required") from exc
        browser = context = None
        with sync_playwright() as playwright:
            try:
                browser, context, page = self._launch(playwright)
                self._bootstrap_page(page)
                categories = self.discover_categories(page)
                snapshot = category_snapshot(categories)
                atomic_write_json(output_path, snapshot)
                atomic_write_json(self.config.category_snapshot_path, snapshot)
                return snapshot
            finally:
                if context is not None:
                    context.close()
                if browser is not None:
                    browser.close()


def save_login_state(config: CrawlConfig, wait_seconds: int | None = None) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise DependencyError("playwright is required") from exc
    crawler = Job51Crawler(config)
    config.headless = False
    with sync_playwright() as playwright:
        browser, context, page = crawler._launch(
            playwright,
            load_storage_state=True,
            prefer_runtime_session=False,
            use_persistent_profile=False,
        )
        replay_context = None
        temporary = config.storage_state_path.with_name(
            f".{config.storage_state_path.name}.{os.getpid()}.{time.time_ns()}.tmp"
        )
        try:
            page.goto(BASE_URL, wait_until="domcontentloaded", timeout=config.timeout_ms)
            if wait_seconds is None:
                input("Complete 51job login in the browser, then press Enter here: ")
            else:
                if wait_seconds < 1 or wait_seconds > 1800:
                    raise ValueError("wait_seconds must be between 1 and 1800")
                time.sleep(wait_seconds)
            crawler._bootstrap_page(page)
            if not _probe_page_login(page):
                raise SessionRequiredError(
                    "51job login was not verified; storage state was not saved"
                )
            crawler._search_once(page, AUTH_SMOKE_FUNCTION_CODE, 1)
            cookies = context.cookies()
            if not cookies:
                raise SessionRequiredError(
                    "51job login had no browser cookies; storage state was not saved"
                )
            config.storage_state_path.parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=str(temporary))
            replay_context = browser.new_context(
                locale="zh-CN",
                storage_state=str(temporary),
            )
            replay_page = replay_context.new_page()
            crawler._bootstrap_page(replay_page)
            if not _probe_page_login(replay_page):
                raise SessionRequiredError(
                    "51job login state could not be replayed in a new browser context"
                )
            crawler._search_once(replay_page, AUTH_SMOKE_FUNCTION_CODE, 1)
            os.replace(temporary, config.storage_state_path)
            return len(cookies)
        finally:
            if replay_context is not None:
                replay_context.close()
            if temporary.exists():
                temporary.unlink()
            context.close()
            browser.close()


def run_job51(config: CrawlConfig | Mapping[str, Any]) -> RunSummary:
    if not isinstance(config, CrawlConfig):
        config = CrawlConfig.from_mapping(config)
    return Job51Crawler(config).run()
