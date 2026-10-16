from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Mapping
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .models import clean_text


ZBJ_SOURCE_CATEGORIES = {
    "rjkf": "软件开发",
    "haibaosj": "海报设计",
    "dzlogo": "广告宣传品设计",
    "appkaifaaa": "APP原生开发",
    "xcxkfzbjzbj": "微信小程序开发",
    "website": "网站定制开发",
}


class CategoryParseError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class OriginalCategory:
    levels: tuple[str, ...]
    source: str
    source_url: str
    identity_verified: bool

    def __post_init__(self) -> None:
        if not self.levels or len(self.levels) > 3:
            raise CategoryParseError("category must contain one to three levels")
        if any(not clean_text(level) for level in self.levels):
            raise CategoryParseError("category levels must not be empty")

    @property
    def path(self) -> str:
        return " > ".join(self.levels)

    def record_fields(self) -> dict[str, object]:
        padded = [*self.levels, None, None, None]
        return {
            "category_level_1_raw": padded[0],
            "category_level_2_raw": padded[1],
            "category_level_3_raw": padded[2],
            "category_path_raw": self.path,
            "category_source": self.source,
            "category_source_url": self.source_url,
            "category_identity_verified": self.identity_verified,
        }

    def to_dict(self) -> dict[str, object]:
        return {**asdict(self), "path": self.path}


def _normalized_url_identity(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    return parsed.netloc.lower(), parsed.path.rstrip("/") or "/"


def category_from_zbj_list_url(list_url: str) -> OriginalCategory:
    parsed = urlparse(list_url)
    path_parts = [part for part in parsed.path.split("/") if part]
    if parsed.netloc.lower() != "www.zbj.com" or len(path_parts) != 3 or path_parts[0] != "xq":
        raise CategoryParseError(f"invalid ZBJ demand list URL: {list_url}")
    slug = path_parts[1]
    label = ZBJ_SOURCE_CATEGORIES.get(slug)
    if label is None or not re.fullmatch(r"r4(?:p\d+)?", path_parts[2]):
        raise CategoryParseError(f"unknown ZBJ source category: {list_url}")
    return OriginalCategory(
        levels=(label,),
        source="source_list_heading",
        source_url=list_url,
        identity_verified=True,
    )


def parse_epwk_detail_category(
    soup: BeautifulSoup,
    *,
    detail_url: str,
    expected_titles: tuple[str, ...],
) -> OriginalCategory:
    detail_host, detail_path = _normalized_url_identity(detail_url)
    task_match = re.fullmatch(r"/(\d+)", detail_path)
    if detail_host != "task.epwk.com" or not task_match:
        raise CategoryParseError(f"invalid EPWK detail URL: {detail_url}")
    task_id = task_match.group(1)

    # EPWK has served both the legacy task_user_info heading and the current
    # Nuxt-rendered title. Keep the identity check strict while accepting the
    # current title container used on live detail pages.
    heading = soup.select_one(
        "div.task_user_info h1, div.task-user-info h1, .contxt .rctr .title"
    )
    heading_text = clean_text(heading.get_text(" ", strip=True) if heading else None)
    expected = {clean_text(value) for value in expected_titles if clean_text(value)}
    if not heading_text or heading_text not in expected:
        raise CategoryParseError(f"EPWK detail title identity mismatch: {task_id}")

    task_number_texts = [
        clean_text(node.get_text(" ", strip=True)) or ""
        for node in soup.select(".task-user-info-action .dib_vm")
    ]
    task_number_verified = any(
        re.search(rf"任务编号\s*[：:]\s*{re.escape(task_id)}(?:\D|$)", text)
        for text in task_number_texts
    )
    if not task_number_verified:
        # The current SSR page keeps the canonical id in taskData even when
        # the old task-user-info action block is absent from the HTML.
        task_number_verified = bool(
            soup.find(
                "script",
                string=re.compile(
                    rf"taskData\s*:\s*\{{[^}}]*task_id\s*:\s*[\"']?{re.escape(task_id)}(?:[\"',}}]|$)",
                    re.DOTALL,
                ),
            )
        )
    if not task_number_verified:
        raise CategoryParseError(f"EPWK task number identity missing: {task_id}")

    breadcrumb = soup.select_one(".cct-bread-crumb")
    if breadcrumb is None:
        raise CategoryParseError(f"EPWK category breadcrumb missing: {task_id}")

    category_levels: list[str] = []
    detail_anchors: list[str] = []
    for anchor in breadcrumb.select("a[href]"):
        text = clean_text(anchor.get_text(" ", strip=True))
        href = urljoin(detail_url, str(anchor.get("href") or ""))
        host, path = _normalized_url_identity(href)
        if not text:
            continue
        if host == "www.epwk.com" and path == "/":
            continue
        if host == "task.epwk.com" and path == "/":
            continue
        if host == detail_host and path == detail_path:
            detail_anchors.append(text)
            continue
        if host == "task.epwk.com" and not re.fullmatch(r"/\d+", path):
            category_levels.append(text)

    if detail_anchors != [heading_text]:
        raise CategoryParseError(f"EPWK current-task breadcrumb mismatch: {task_id}")
    if len(category_levels) != 3:
        raise CategoryParseError(
            f"EPWK expected three category levels, got {len(category_levels)}: {task_id}"
        )
    return OriginalCategory(
        levels=tuple(category_levels),
        source="detail_breadcrumb",
        source_url=detail_url,
        identity_verified=True,
    )


def parse_shixian_detail_category(
    soup: BeautifulSoup,
    *,
    detail_url: str,
    expected_title: str,
) -> OriginalCategory:
    detail_host, detail_path = _normalized_url_identity(detail_url)
    if detail_host != "shixian.com" or not re.fullmatch(r"/jobs/\d+", detail_path):
        raise CategoryParseError(f"invalid Shixian detail URL: {detail_url}")
    heading = soup.select_one("article.job-show h1.title")
    if heading is None:
        raise CategoryParseError(f"Shixian detail heading missing: {detail_url}")
    heading_copy = BeautifulSoup(str(heading), "html.parser")
    for type_node in heading_copy.select("span.type"):
        type_node.decompose()
    heading_title = clean_text(heading_copy.get_text(" ", strip=True))
    if heading_title != clean_text(expected_title):
        raise CategoryParseError(f"Shixian detail title identity mismatch: {detail_url}")

    anchors = soup.select("ol.breadcrumb.jobs > li > a[href]")
    if len(anchors) != 2:
        raise CategoryParseError(
            f"Shixian expected location and category breadcrumb: {detail_url}"
        )
    category_anchor = anchors[1]
    category_text = clean_text(category_anchor.get_text(" ", strip=True))
    category_url = urljoin(detail_url, str(category_anchor.get("href") or ""))
    parsed_category = urlparse(category_url)
    category_parts = [part for part in parsed_category.path.split("/") if part]
    if (
        not category_text
        or parsed_category.netloc.lower() != detail_host
        or len(category_parts) != 3
        or category_parts[0] != "job"
    ):
        raise CategoryParseError(f"Shixian category link invalid: {detail_url}")
    return OriginalCategory(
        levels=(category_text,),
        source="detail_breadcrumb",
        source_url=category_url,
        identity_verified=True,
    )


def validate_original_category_fields(
    site_name: str,
    fields: Mapping[str, object],
    *,
    detail_url: str | None = None,
) -> tuple[str, ...]:
    levels = tuple(
        str(fields.get(name) or "").strip()
        for name in (
            "category_level_1_raw",
            "category_level_2_raw",
            "category_level_3_raw",
        )
    )
    nonempty_levels = tuple(level for level in levels if level)
    expected_counts = {"zbj": 1, "epwk": 3, "shixian": 1}
    expected_sources = {
        "zbj": {"source_list_heading"},
        "epwk": {
            "detail_breadcrumb",
            "search_indexed_original_category_listing+original_site_taxonomy",
        },
        "shixian": {"detail_breadcrumb"},
    }
    if site_name not in expected_counts:
        raise CategoryParseError(f"unsupported category site: {site_name}")
    if len(nonempty_levels) != expected_counts[site_name] or levels[: len(nonempty_levels)] != nonempty_levels:
        raise CategoryParseError(f"invalid category level count for {site_name}")
    if str(fields.get("category_path_raw") or "").strip() != " > ".join(nonempty_levels):
        raise CategoryParseError(f"category path does not match levels for {site_name}")
    category_source = str(fields.get("category_source") or "")
    if category_source not in expected_sources[site_name]:
        raise CategoryParseError(f"invalid category source for {site_name}")
    if fields.get("category_identity_verified") is not True:
        raise CategoryParseError(f"category identity is not verified for {site_name}")

    source_url = str(fields.get("category_source_url") or "").strip()
    source_host, source_path = _normalized_url_identity(source_url)
    if site_name == "zbj":
        if category_from_zbj_list_url(source_url).levels != nonempty_levels:
            raise CategoryParseError("ZBJ category source and level mismatch")
    elif site_name == "epwk":
        if category_source == "detail_breadcrumb":
            if source_host != "task.epwk.com" or not re.fullmatch(r"/\d+", source_path):
                raise CategoryParseError("invalid EPWK category evidence URL")
            if detail_url and _normalized_url_identity(detail_url) != (source_host, source_path):
                raise CategoryParseError(
                    "EPWK category evidence URL does not match detail"
                )
        elif source_host != "task.epwk.com" or not re.fullmatch(
            r"/[a-z0-9_-]+", source_path
        ):
            raise CategoryParseError("invalid EPWK indexed category evidence URL")
    elif source_host != "shixian.com" or not re.fullmatch(r"/job/[^/]+/[^/]+", source_path):
        raise CategoryParseError("invalid Shixian category evidence URL")
    return nonempty_levels
