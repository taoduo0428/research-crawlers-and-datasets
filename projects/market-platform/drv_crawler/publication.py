from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any


CHINA_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")

_ABSOLUTE_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>20\d{2})\s*(?:[-/.]|年)\s*"
    r"(?P<month>\d{1,2})\s*(?:[-/.]|月)\s*"
    r"(?P<day>\d{1,2})(?:日)?(?!\d)"
)
_MINUTE_OR_HOUR_RE = re.compile(
    r"^(?P<count>\d+)\s*(?P<unit>分钟|小时)前$"
)
_DAY_RE = re.compile(r"^(?P<count>\d+)\s*天前$")
_WEEK_RE = re.compile(r"^(?P<count>\d+)\s*(?:周|星期)前$")
_MONTH_RE = re.compile(r"^(?:大约|约)?\s*(?P<count>\d+)\s*个月前$")
_YEAR_RE = re.compile(r"^(?:大约|约)?\s*(?P<count>\d+)\s*年前$")


_YEAR_MORE_RE = re.compile(r'^(?P<count>\d+)\s*\u5e74\u591a\u524d$')
_YEAR_NEAR_RE = re.compile(r'^\u63a5\u8fd1\s*(?P<count>\d+)\s*\u5e74\u524d$')

def subtract_calendar_months(value: date, months: int) -> date:
    if months < 0:
        raise ValueError("months must not be negative")
    month_index = value.year * 12 + value.month - 1 - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


@dataclass(frozen=True, slots=True)
class PublicationDecision:
    status: str
    normalized_date: date | None = None
    interval_start: date | None = None
    interval_end: date | None = None

    @property
    def included(self) -> bool:
        return self.status == "include"

    def to_dict(self) -> dict[str, str | None]:
        return {
            "status": self.status,
            "normalized_date": (
                self.normalized_date.isoformat() if self.normalized_date else None
            ),
            "interval_start": self.interval_start.isoformat() if self.interval_start else None,
            "interval_end": self.interval_end.isoformat() if self.interval_end else None,
        }


class PublicationFilter:
    def __init__(
        self,
        *,
        recent_months: int | None = None,
        published_since: date | str | None = None,
        as_of: date | None = None,
    ) -> None:
        if (recent_months is None) == (published_since is None):
            raise ValueError("set exactly one of recent_months or published_since")
        self.as_of = as_of or datetime.now(CHINA_TIMEZONE).date()
        self.recent_months = recent_months
        if recent_months is not None:
            if recent_months <= 0:
                raise ValueError("recent_months must be positive")
            self.cutoff = subtract_calendar_months(self.as_of, recent_months)
            self.source = "recent_months"
        else:
            try:
                self.cutoff = (
                    published_since
                    if isinstance(published_since, date)
                    else date.fromisoformat(str(published_since))
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("published_since must use YYYY-MM-DD") from exc
            self.source = "published_since"
        if self.cutoff > self.as_of:
            raise ValueError("publication cutoff must not be after the reference date")

    def _decide_date(self, value: date) -> PublicationDecision:
        if value > self.as_of:
            return PublicationDecision("future", normalized_date=value)
        if value < self.cutoff:
            return PublicationDecision("too_old", normalized_date=value)
        return PublicationDecision("include", normalized_date=value)

    def _decide_date_interval(
        self,
        earliest: date,
        latest: date,
    ) -> PublicationDecision:
        if latest < self.cutoff:
            status = "too_old"
        elif earliest >= self.cutoff:
            status = "include"
        else:
            status = "uncertain"
        return PublicationDecision(
            status,
            interval_start=earliest,
            interval_end=latest,
        )

    def _decide_year_interval(
        self, years: int, *, approaching: bool
    ) -> PublicationDecision:
        if approaching:
            earliest = subtract_calendar_months(self.as_of, 12 * years)
            latest = subtract_calendar_months(self.as_of, 12 * max(years - 1, 0))
        else:
            earliest = (
                subtract_calendar_months(self.as_of, 12 * (years + 1))
                + timedelta(days=1)
            )
            latest = subtract_calendar_months(self.as_of, 12 * years)
        return self._decide_date_interval(earliest, latest)

    def _decide_month_interval(self, months: int) -> PublicationDecision:
        latest = subtract_calendar_months(self.as_of, months)
        earliest = subtract_calendar_months(self.as_of, months + 1) + timedelta(days=1)
        if latest < self.cutoff:
            status = "too_old"
        elif earliest >= self.cutoff:
            status = "include"
        else:
            status = "uncertain"
        return PublicationDecision(
            status,
            interval_start=earliest,
            interval_end=latest,
        )

    def decide(self, raw: str | None) -> PublicationDecision:
        value = " ".join(str(raw or "").replace("\u00a0", " ").split())
        if not value:
            return PublicationDecision("unknown")

        absolute = _ABSOLUTE_DATE_RE.search(value)
        if absolute:
            try:
                parsed = date(
                    int(absolute.group("year")),
                    int(absolute.group("month")),
                    int(absolute.group("day")),
                )
            except ValueError:
                return PublicationDecision("unknown")
            return self._decide_date(parsed)

        relative = value.removesuffix("发布").strip()
        if relative == "刚刚":
            return self._decide_date_interval(
                self.as_of - timedelta(days=1),
                self.as_of,
            )
        if relative in {"今天", "今日"}:
            return self._decide_date(self.as_of)
        if relative == "昨天":
            return self._decide_date(self.as_of - timedelta(days=1))
        if relative == "前天":
            return self._decide_date(self.as_of - timedelta(days=2))
        try:
            if match := _MINUTE_OR_HOUR_RE.fullmatch(relative):
                count = int(match.group("count"))
                maximum = 1440 if match.group("unit") == "分钟" else 24
                if not 0 < count <= maximum:
                    return PublicationDecision("unknown")
                if count == maximum:
                    return self._decide_date(self.as_of - timedelta(days=1))
                return self._decide_date_interval(
                    self.as_of - timedelta(days=1),
                    self.as_of,
                )
            if match := _DAY_RE.fullmatch(relative):
                count = int(match.group("count"))
                return (
                    self._decide_date(self.as_of - timedelta(days=count))
                    if count > 0
                    else PublicationDecision("unknown")
                )
            if match := _WEEK_RE.fullmatch(relative):
                count = int(match.group("count"))
                return (
                    self._decide_date(self.as_of - timedelta(days=7 * count))
                    if count > 0
                    else PublicationDecision("unknown")
                )
            if match := _MONTH_RE.fullmatch(relative):
                count = int(match.group("count"))
                return (
                    self._decide_month_interval(count)
                    if count > 0
                    else PublicationDecision("unknown")
                )
            if match := _YEAR_RE.fullmatch(relative):
                count = int(match.group("count"))
                return (
                    self._decide_year_interval(count, approaching=False)
                    if count > 0
                    else PublicationDecision("unknown")
                )
            if match := _YEAR_MORE_RE.fullmatch(relative):
                count = int(match.group("count"))
                return (
                    self._decide_year_interval(count, approaching=False)
                    if count > 0
                    else PublicationDecision("unknown")
                )
            if match := _YEAR_NEAR_RE.fullmatch(relative):
                count = int(match.group("count"))
                return (
                    self._decide_year_interval(count, approaching=True)
                    if count > 0
                    else PublicationDecision("unknown")
                )
        except (OverflowError, ValueError):
            return PublicationDecision("unknown")
        return PublicationDecision("unknown")

    def describe(self) -> dict[str, Any]:
        return {
            "mode": "strict",
            "source": self.source,
            "recent_months": self.recent_months,
            "as_of": self.as_of.isoformat(),
            "cutoff": self.cutoff.isoformat(),
            "cutoff_inclusive": True,
            "timezone": "Asia/Shanghai",
            "unknown_policy": "exclude",
            "uncertain_policy": "exclude",
        }
