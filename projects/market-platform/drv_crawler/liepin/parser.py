from __future__ import annotations

import re
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from .errors import SchemaChangedError
from .models import CareerCategory, LiepinJobRecord, clean_text


_EDUCATION_VALUES = {
    "博士",
    "MBA/EMBA",
    "硕士",
    "本科",
    "大专",
    "中专/中技",
    "高中",
    "初中及以下",
    "学历不限",
}
_EXPERIENCE_RE = re.compile(r"(?:\d+(?:-\d+)?年|经验不限|应届|在校)")
_CAREER_PATH_RE = re.compile(r"^/career/[^/?#]+/$", re.IGNORECASE)
_PAGED_CAREER_PATH_RE = re.compile(
    r"^/career/(?P<slug>[^/?#]+)/pn(?P<page>\d+)/$", re.IGNORECASE
)
_BASE_CAREER_PATH_RE = re.compile(r"^/career/(?P<slug>[^/?#]+)/$", re.IGNORECASE)
_EMPTY_CAREER_RE = re.compile(r"(?:暂无.*职位|暂时没有合适的职位)")


def _text(element: Tag | None) -> str | None:
    return clean_text(element.get_text(" ", strip=True)) if element else None


def _detail_text(element: Tag | None) -> str | None:
    """Normalize a detail JD while retaining section/item line boundaries."""
    if element is None:
        return None
    lines: list[str] = []
    for raw_line in element.get_text("\n", strip=True).splitlines():
        line = clean_text(raw_line)
        if line and (not lines or line != lines[-1]):
            lines.append(line)
    return "\n".join(lines) or None


def parse_detail_description(html: str) -> str | None:
    """Extract Liepin's real detail-page JD (职位职责/要求/技能要求).

    The list-card HTML does not contain the job description. Current detail
    pages expose it in ``dd[data-selector=job-intro-content]`` inside the
    ``职位介绍`` paragraph. The fallbacks keep the parser useful when the
    attribute is omitted by an older page variant, without treating unrelated
    metadata as a JD.
    """
    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one('dd[data-selector="job-intro-content"]')
    if content is None:
        for paragraph in soup.select("dl.paragraph"):
            heading = _text(paragraph.select_one("dt"))
            if heading == "职位介绍":
                content = paragraph.select_one("dd")
                break
    if content is None:
        content = soup.select_one(
            '[data-selector="job-intro-content"], .job-intro-content'
        )
    return _detail_text(content)


def _is_liepin_career_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.hostname in {"www.liepin.com", "liepin.com"} and bool(
        _CAREER_PATH_RE.match(parsed.path)
    )


def parse_taxonomy(html: str, base_url: str = "https://www.liepin.com/") -> list[CareerCategory]:
    soup = BeautifulSoup(html, "html.parser")
    output: list[CareerCategory] = []
    seen_urls: set[str] = set()
    for first in soup.select("li.hot-jobs-first-item"):
        first_anchor = first.select_one("a.hot-jobs-first-title")
        level_1 = _text(first_anchor)
        if not level_1:
            continue
        for second in first.select("div.hot-jobs-second-item"):
            second_anchor = second.select_one("a.hot-jobs-second-title")
            level_2 = _text(second_anchor)
            if not level_2:
                continue
            for third_anchor in second.select("a.hot-jobs-three-title"):
                level_3 = _text(third_anchor)
                href = clean_text(third_anchor.get("href"))
                if not level_3 or not href:
                    continue
                url = urljoin(base_url, href)
                if not _is_liepin_career_url(url) or url in seen_urls:
                    continue
                seen_urls.add(url)
                output.append(CareerCategory(level_1, level_2, level_3, url))
    return output


def classify_access_gate(html: str, final_url: str) -> str | None:
    """Classify a Liepin access gate without conflating login and verification."""
    parsed = urlparse(final_url)
    hostname = (parsed.hostname or "").lower()
    if hostname == "passport.liepin.com" or hostname.endswith(".passport.liepin.com"):
        return "login_required"
    if hostname == "safe.liepin.com" or hostname.endswith(".safe.liepin.com"):
        return "security_challenge"
    if hostname == "wow.liepin.com" or hostname.endswith(".wow.liepin.com"):
        return "security_transit"
    soup = BeautifulSoup(html[:100_000], "html.parser")
    title = (_text(soup.title) or "").lower()
    login_signals = (
        "请修改密码",
        "登录失效",
        "请重新登录",
    )
    if any(signal in title for signal in login_signals):
        return "login_required"
    challenge_signals = (
        "猎聘安全中心",
        "安全验证",
        "人机验证",
        "访问异常",
    )
    if any(signal in title for signal in challenge_signals):
        return "security_challenge"

    structured_challenge = soup.select_one(
        '[class*="geetest"], [id*="geetest"], '
        '[class*="captcha"], [id*="captcha"], '
        'iframe[src*="captcha"], iframe[src*="verify"]'
    )
    if structured_challenge is not None:
        return "security_challenge"

    body = _text(soup.body) or ""
    # Normal job descriptions and company names may contain "安全中心".
    # Only explicit gate instructions on a short page count as a challenge.
    if len(body) <= 5000 and any(signal in body for signal in login_signals):
        return "login_required"
    gate_signals = (
        "请完成安全验证",
        "请进行安全验证",
        "请完成人机验证",
        "请拖动滑块",
        "请按住滑块",
        "访问存在异常",
        "当前访问异常",
    )
    if len(body) <= 5000 and any(signal in body for signal in gate_signals):
        return "security_challenge"
    return None


def looks_like_challenge(html: str, final_url: str) -> bool:
    return classify_access_gate(html, final_url) is not None


def looks_like_career_page(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    return bool(
        soup.select_one(".job-list-box")
        or soup.select_one(".job-card-pc-container")
        or soup.find(string=_EMPTY_CAREER_RE)
    )


def is_empty_career_page(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    return bool(soup.find(string=_EMPTY_CAREER_RE))


def count_job_cards(html: str) -> int:
    return len(BeautifulSoup(html, "html.parser").select(".job-card-pc-container"))


def has_pagination_evidence(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    return bool(
        soup.select_one('[data-selector="pagintion-item-selector"]')
        or soup.select_one("[data-currentpage]")
    )


def _career_url_parts(url: str) -> tuple[str, str, int]:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if hostname not in {"www.liepin.com", "liepin.com"}:
        raise SchemaChangedError("pagination URL left the official Liepin host")
    paged_match = _PAGED_CAREER_PATH_RE.match(parsed.path)
    if paged_match:
        return (
            hostname,
            unquote(paged_match.group("slug")).lower(),
            int(paged_match.group("page")),
        )
    base_match = _BASE_CAREER_PATH_RE.match(parsed.path)
    if base_match:
        return hostname, unquote(base_match.group("slug")).lower(), 0
    raise SchemaChangedError("pagination URL is not an official career page")


def career_page_index(url: str, *, category_url: str) -> int:
    _, category_slug, _ = _career_url_parts(category_url)
    _, current_slug, page_index = _career_url_parts(url)
    if current_slug != category_slug:
        raise SchemaChangedError("career pagination changed category identity")
    return page_index


def parse_next_page_url(
    html: str,
    *,
    current_url: str,
    category_url: str,
) -> str | None:
    current_page = career_page_index(current_url, category_url=category_url)
    soup = BeautifulSoup(html, "html.parser")

    indexed_urls: dict[int, str] = {}
    for anchor in soup.select('a[data-selector="pagintion-item-selector"]'):
        href = clean_text(anchor.get("href"))
        if not href:
            continue
        candidate = urljoin(current_url, href)
        candidate_page = career_page_index(candidate, category_url=category_url)
        indexed_urls[candidate_page] = candidate

    next_page = current_page + 1
    if next_page in indexed_urls:
        return indexed_urls[next_page]
    if any(page > next_page for page in indexed_urls):
        raise SchemaChangedError("career pagination skipped the immediate next page")
    return None


def has_nonzero_numeric_salary(record: LiepinJobRecord) -> bool:
    return record.has_nonzero_numeric_salary


def _title_from_card(card: Tag) -> str | None:
    titled = card.select_one(".job-title-box [title]")
    if titled:
        return clean_text(titled.get("title")) or _text(titled)
    box = card.select_one(".job-title-box")
    if not box:
        return None
    location = box.select_one(".job-dq-box")
    if location:
        location.extract()
    return _text(box)


def _classify_labels(labels: list[str]) -> tuple[str | None, str | None]:
    experience = next((item for item in labels if _EXPERIENCE_RE.search(item)), None)
    education = next((item for item in labels if item in _EDUCATION_VALUES), None)
    return experience, education


def _classify_company_tags(tags: list[str]) -> tuple[str | None, str | None]:
    size = next((item for item in tags if "人" in item or "规模" in item), None)
    industry = next((item for item in tags if item != size), None)
    return industry, size


def parse_job_cards(
    html: str,
    category: CareerCategory,
    *,
    per_category: int,
    page_no: int = 1,
    list_position_offset: int = 0,
) -> list[LiepinJobRecord]:
    soup = BeautifulSoup(html, "html.parser")
    output: list[LiepinJobRecord] = []
    seen_job_ids: set[str] = set()
    for card in soup.select(".job-card-pc-container"):
        anchor = card.select_one('a[data-nick="job-detail-job-info"]')
        if not anchor:
            anchor = card.select_one('a[href*="/job/"]')
        href = clean_text(anchor.get("href")) if anchor else None
        title = _title_from_card(card)
        if not href or not title:
            continue
        detail_url = urljoin(category.url, href)
        job_id = clean_text(anchor.get("data-jobid")) or clean_text(
            anchor.get("data-jobId")
        )
        if not job_id:
            match = re.search(r"/job/(\d+)\.shtml", detail_url)
            job_id = match.group(1) if match else detail_url
        if job_id in seen_job_ids:
            continue
        seen_job_ids.add(job_id)

        labels = [
            value
            for item in card.select(".job-labels-box .labels-tag, .job-labels-box span")
            if (value := _text(item))
        ]
        labels = list(dict.fromkeys(labels))
        experience, education = _classify_labels(labels)
        company_tags = [
            value
            for item in card.select(".company-tags-box span")
            if (value := _text(item))
        ]
        if not company_tags:
            combined_tags = _text(card.select_one(".company-tags-box"))
            company_tags = [combined_tags] if combined_tags else []
        industry, size = _classify_company_tags(company_tags)

        location = _text(card.select_one(".job-dq-box .ellipsis-1"))
        if not location:
            raw_location = _text(card.select_one(".job-dq-box"))
            location = raw_location.strip("【】[] ") if raw_location else None

        right_box = card.select_one(".job-card-right-box")
        recruiter_parts = [
            value
            for item in right_box.select("span, div") if (value := _text(item))
        ] if right_box else []
        recruiter_parts = list(dict.fromkeys(recruiter_parts))
        recruiter_name = _text(card.select_one(".recruiter-name"))
        recruiter_title = next(
            (item for item in recruiter_parts if item != recruiter_name), None
        )

        output.append(
            LiepinJobRecord(
                category=category,
                job_id=job_id,
                job_kind=clean_text(anchor.get("data-jobkind")),
                title=title,
                salary_raw=_text(card.select_one(".job-salary")),
                detail_url=detail_url,
                list_position=list_position_offset + len(output) + 1,
                page_no=page_no,
                location=location,
                experience=experience,
                education=education,
                company_name=_text(card.select_one(".company-name")),
                company_industry=industry,
                company_size=size,
                labels=labels,
                recruiter_name=recruiter_name,
                recruiter_title=recruiter_title,
            )
        )
        if len(output) >= per_category:
            break
    return output
