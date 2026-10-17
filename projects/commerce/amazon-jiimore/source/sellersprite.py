"""SellerSprite API crawler.

The module keeps authentication lazy: importing it and showing ``--help`` do
not read the local cookie file.  Network requests are made only by the three
metric functions below, with proxy and timeout loaded from ``project_config``.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import requests

try:  # Works both as ``python Amazon/sellersprite.py`` and from the tests.
    from project_config import DATA_ROOT, SCRIPT_DIR, get_proxies, load_config
except ModuleNotFoundError:  # pragma: no cover - package import fallback
    from Amazon.project_config import DATA_ROOT, SCRIPT_DIR, get_proxies, load_config


DATA_DIR = DATA_ROOT / 'sellersprite'
ASIN_PATH = DATA_ROOT / 'ASIN.txt'
COOKIE_PATH = SCRIPT_DIR / 'sellersprite_cookies.json'

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
    "cache-control": "no-cache",
    "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
    "origin": "https://www.sellersprite.com",
    "pragma": "no-cache",
    "referer": "https://www.sellersprite.com/v3/competitor-lookup?market=US&monthName=bsr_sales_nearly&asins=%5B%5D&page=1&nodeIdPaths=%5B%5D&symbolFlag=true&size=60&order%5Bfield%5D=total_units&order%5Bdesc%5D=true",
    "sec-ch-ua": "\"Microsoft Edge\";v=\"123\", \"Not:A-Brand\";v=\"8\", \"Chromium\";v=\"123\"",
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": "\"Windows\"",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
}

MONTHLY_FIELDS = ['ASIN', '\u6708\u4efd', '\u6708\u9500\u91cf', '\u6708\u9500\u552e\u989d', '\u5e73\u5747\u5355\u4ef7']
REVIEW_FIELDS = ['ASIN', '\u6708\u4efd', '\u8bc4\u5206\u6570\u589e\u957f\u503c', '\u7559\u8bc4\u7387']
KEEPA_FIELDS = ['ASIN', '\u65e5\u671f', '\u8bc4\u5206', '\u8bc4\u5206\u6570', '\u4ef7\u683c']


class SellerSpriteError(RuntimeError):
    """Base class for recoverable crawler errors."""


class CookieError(SellerSpriteError):
    """The local SellerSprite cookie export is missing or unusable."""


class AuthenticationError(SellerSpriteError):
    """The endpoint rejected the request as unauthenticated/rate-limited."""

    def __init__(self, status: int, url: str):
        self.status = status
        self.url = url
        super().__init__(f'HTTP {status}')


class ResponseError(SellerSpriteError):
    """The endpoint returned an unusable HTTP/JSON payload."""


def _result(asin: str, status: str, rows: int = 0, path: Path | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {'asin': asin, 'status': status, 'rows': rows}
    if path is not None:
        value['path'] = str(path)
    return value


def load_cookies(path: Path | str = COOKIE_PATH) -> dict[str, Any]:
    """Load the private cookie export only immediately before a request.

    Cookie values are never printed.  A missing/invalid export gets an
    actionable message instead of a low-level JSON or file exception.
    """

    path = Path(path)
    if not path.is_file():
        raise CookieError(
            f'\u672a\u627e\u5230\u5356\u5bb6\u7cbe\u7075 Cookie \u6587\u4ef6\uff1a{path.name}\u3002\u8bf7\u5148\u5bfc\u51fa\u767b\u5f55 Cookie \u5230 Amazon \u76ee\u5f55\u3002'
        )
    try:
        with path.open('r', encoding='utf-8-sig') as handle:
            value = json.load(handle)
    except (OSError, ValueError) as error:
        raise CookieError(
            f'\u5356\u5bb6\u7cbe\u7075 Cookie \u6587\u4ef6\u65e0\u6cd5\u8bfb\u53d6\uff1a{path.name}\u3002\u8bf7\u91cd\u65b0\u5bfc\u51fa\u6709\u6548 Cookie\u3002'
        ) from error
    if not isinstance(value, dict) or not value.get('rank-login-user'):
        raise CookieError(
            f'\u5356\u5bb6\u7cbe\u7075 Cookie \u6587\u4ef6\u7f3a\u5c11\u5fc5\u8981\u767b\u5f55 Cookie\uff1a{path.name}\u3002\u8bf7\u91cd\u65b0\u5bfc\u51fa\u540e\u518d\u8fd0\u884c\u3002'
        )
    return value


def _request_options() -> tuple[dict[str, str], float]:
    config = load_config()
    return get_proxies(config), float(config['request_timeout'])


def _request_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    """Make one request with current config and lazily loaded cookies."""

    proxies, timeout = _request_options()
    cookies = load_cookies()
    options = {
        'headers': dict(HEADERS),
        'cookies': cookies,
        'proxies': proxies,
        'timeout': timeout,
    }
    options.update(kwargs)
    if method == 'post':
        response = requests.post(url, **options)
    elif method == 'get':
        response = requests.get(url, **options)
    else:  # pragma: no cover - internal guard
        raise ValueError(f'unsupported request method: {method}')

    status = getattr(response, 'status_code', None)
    if status in (401, 403, 429):
        raise AuthenticationError(int(status), url)
    if isinstance(status, int) and status >= 400:
        raise ResponseError(f'\u8bf7\u6c42\u5931\u8d25\uff1aHTTP {status}')
    try:
        payload = response.json()
    except Exception as error:
        raise ResponseError('\u63a5\u53e3\u8fd4\u56de\u4e0d\u662f\u6709\u6548 JSON\uff0c\u672a\u5199\u5165\u7ed3\u679c\u3002') from error
    if not isinstance(payload, dict):
        raise ResponseError('\u63a5\u53e3 JSON \u9876\u5c42\u4e0d\u662f\u5bf9\u8c61\uff0c\u672a\u5199\u5165\u7ed3\u679c\u3002')
    if payload.get('success') is False:
        raise ResponseError('\u63a5\u53e3\u8fd4\u56de\u5931\u8d25\uff0c\u672a\u5199\u5165\u7ed3\u679c\u3002')
    return payload


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    try:
        with path.open('r', encoding='utf-8-sig', newline='') as handle:
            return list(csv.DictReader(handle))
    except (OSError, csv.Error) as error:
        raise ResponseError(f'\u7ed3\u679c\u6587\u4ef6\u65e0\u6cd5\u8bfb\u53d6\uff1a{path.name}') from error


def _has_asin(path: Path, asin: str) -> bool:
    return any(str(row.get('ASIN') or '').strip() == asin for row in _read_rows(path))


def _write_rows_atomic(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    """Rewrite one CSV atomically, preserving existing rows."""

    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_rows(path)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            'w', encoding='utf-8-sig', newline='', dir=path.parent,
            prefix=path.name + '.', suffix='.tmp', delete=False,
        ) as handle:
            temporary = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
            writer.writeheader()
            writer.writerows({field: row.get(field, '') for field in fields} for row in existing + rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def date_index(time_list: list[str]) -> list[int]:
    """Return the latest point for each calendar day, as the old crawler did."""

    time_dict: defaultdict[Any, list[tuple[datetime, int]]] = defaultdict(list)
    for index, value in enumerate(time_list):
        parsed = datetime.strptime(value, '%Y-%m-%d %H:%M')
        time_dict[parsed.date()].append((parsed, index))
    return [max(values, key=lambda pair: pair[0])[1] for values in time_dict.values()]


def get_monthly_sales(asin: str) -> dict[str, Any]:
    print(f'\u6b63\u5728\u91c7\u96c6 {asin} \u7684\u6708\u9500\u91cf\u6570\u636e...', flush=True)
    path = DATA_DIR / 'monthly_sales.csv'
    if _has_asin(path, asin):
        print('\u6570\u636e\u5df2\u5b58\u5728\uff0c\u65e0\u9700\u6dfb\u52a0\u3002', flush=True)
        return _result(asin, 'skipped', path=path)

    url = 'https://www.sellersprite.com/v2/competitor-lookup/chart-monthly.json'
    payload = _request_json('post', url, data={'asin': asin, 'marketId': '1'})
    data = payload.get('data')
    chart_data = data.get('chartData') if isinstance(data, dict) else None
    if not isinstance(chart_data, dict) or not chart_data:
        print(f'{asin} \u6708\u9500\u91cf\u63a5\u53e3\u4e3a\u7a7a\uff0c\u672a\u5199\u5165\u5360\u4f4d\u884c\u3002', flush=True)
        return _result(asin, 'empty', path=path)

    rows = []
    for month, value in chart_data.items():
        if not isinstance(value, dict):
            raise ResponseError('\u6708\u9500\u91cf\u63a5\u53e3\u5b57\u6bb5\u7ed3\u6784\u65e0\u6548\uff0c\u672a\u5199\u5165\u7ed3\u679c\u3002')
        row = {
            'ASIN': asin,
            '\u6708\u4efd': month,
            '\u6708\u9500\u91cf': value.get('totalUnits', ''),
            '\u6708\u9500\u552e\u989d': value.get('totalAmount', ''),
            '\u5e73\u5747\u5355\u4ef7': value.get('averagePrice', ''),
        }
        print(row, flush=True)
        rows.append(row)
    if rows:
        _write_rows_atomic(path, MONTHLY_FIELDS, rows)
    return _result(asin, 'success', len(rows), path)


def get_reviews(asin: str) -> dict[str, Any]:
    print(f'\u6b63\u5728\u91c7\u96c6 {asin} \u7684\u7559\u8bc4\u7387\u6570\u636e...', flush=True)
    path = DATA_DIR / 'reviews.csv'
    if _has_asin(path, asin):
        print('\u6570\u636e\u5df2\u5b58\u5728\uff0c\u65e0\u9700\u6dfb\u52a0\u3002', flush=True)
        return _result(asin, 'skipped', path=path)

    url = f'https://www.sellersprite.com/v3/api/trends/reviews/1/{asin}'
    payload = _request_json('post', url, data=json.dumps({}, separators=(',', ':')))
    data = payload.get('data')
    if not isinstance(data, dict):
        raise ResponseError('\u7559\u8bc4\u7387\u63a5\u53e3\u5b57\u6bb5\u7ed3\u6784\u65e0\u6548\uff0c\u672a\u5199\u5165\u7ed3\u679c\u3002')
    delta = data.get('dalta')
    delta_month = data.get('daltaMonth')
    rate = data.get('rate')
    if not isinstance(delta, list) or not isinstance(delta_month, list) or not isinstance(rate, list):
        raise ResponseError('\u7559\u8bc4\u7387\u63a5\u53e3\u5b57\u6bb5\u7ed3\u6784\u65e0\u6548\uff0c\u672a\u5199\u5165\u7ed3\u679c\u3002')
    if not delta_month:
        print(f'{asin} \u7559\u8bc4\u7387\u63a5\u53e3\u4e3a\u7a7a\uff0c\u672a\u5199\u5165\u5360\u4f4d\u884c\u3002', flush=True)
        return _result(asin, 'empty', path=path)
    if not (len(delta) == len(delta_month) == len(rate)):
        raise ResponseError('\u7559\u8bc4\u7387\u63a5\u53e3\u6570\u7ec4\u957f\u5ea6\u4e0d\u4e00\u81f4\uff0c\u672a\u5199\u5165\u7ed3\u679c\u3002')

    rows = []
    for index, month in enumerate(delta_month):
        if not isinstance(month, str) or len(month) < 6:
            raise ResponseError('\u7559\u8bc4\u7387\u6708\u4efd\u5b57\u6bb5\u65e0\u6548\uff0c\u672a\u5199\u5165\u7ed3\u679c\u3002')
        row = {
            'ASIN': asin,
            '\u6708\u4efd': month[:4] + '-' + month[4:],
            '\u8bc4\u5206\u6570\u589e\u957f\u503c': delta[index],
            '\u7559\u8bc4\u7387': rate[index],
        }
        print(row, flush=True)
        rows.append(row)
    _write_rows_atomic(path, REVIEW_FIELDS, rows)
    return _result(asin, 'success', len(rows), path)


def get_keepa(asin: str) -> dict[str, Any]:
    print(f'\u6b63\u5728\u91c7\u96c6 {asin} \u7684\u5386\u53f2\u8d8b\u52bf\u6570\u636e...', flush=True)
    path = DATA_DIR / 'keepa.csv'
    if _has_asin(path, asin):
        print('\u6570\u636e\u5df2\u5b58\u5728\uff0c\u65e0\u9700\u6dfb\u52a0\u3002', flush=True)
        return _result(asin, 'skipped', path=path)

    url = f'https://www.sellersprite.com/v2/keepa/subTrend/us/{asin}'
    payload = _request_json('get', url, params={'parentModule': 'ARA'})
    data = payload.get('data')
    keepa_data = data.get('keepa') if isinstance(data, dict) else None
    if not isinstance(keepa_data, dict):
        raise ResponseError('Keepa \u63a5\u53e3\u5b57\u6bb5\u7ed3\u6784\u65e0\u6548\uff0c\u672a\u5199\u5165\u7ed3\u679c\u3002')
    times = keepa_data.get('times')
    values = keepa_data.get('keepa')
    if not isinstance(times, list) or not isinstance(values, dict):
        raise ResponseError('Keepa \u63a5\u53e3\u5b57\u6bb5\u7ed3\u6784\u65e0\u6548\uff0c\u672a\u5199\u5165\u7ed3\u679c\u3002')
    ratings = values.get('rating')
    reviews = values.get('reviews')
    prices = values.get('price')
    if not all(isinstance(value, list) for value in (ratings, reviews, prices)):
        raise ResponseError('Keepa \u63a5\u53e3\u5b57\u6bb5\u6570\u7ec4\u65e0\u6548\uff0c\u672a\u5199\u5165\u7ed3\u679c\u3002')
    if not times:
        print(f'{asin} Keepa \u63a5\u53e3\u4e3a\u7a7a\uff0c\u672a\u5199\u5165\u5360\u4f4d\u884c\u3002', flush=True)
        return _result(asin, 'empty', path=path)
    if not (len(times) == len(ratings) == len(reviews) == len(prices)):
        raise ResponseError('Keepa \u63a5\u53e3\u6570\u7ec4\u957f\u5ea6\u4e0d\u4e00\u81f4\uff0c\u672a\u5199\u5165\u7ed3\u679c\u3002')

    rows = []
    for index in date_index(times):
        row = {
            'ASIN': asin,
            '\u65e5\u671f': times[index].split(' ')[0],
            '\u8bc4\u5206': ratings[index],
            '\u8bc4\u5206\u6570': reviews[index],
            '\u4ef7\u683c': prices[index],
        }
        print(row, flush=True)
        rows.append(row)
    if not rows:
        print(f'{asin} Keepa \u63a5\u53e3\u6ca1\u6709\u53ef\u7528\u65e5\u671f\uff0c\u672a\u5199\u5165\u5360\u4f4d\u884c\u3002', flush=True)
        return _result(asin, 'empty', path=path)
    _write_rows_atomic(path, KEEPA_FIELDS, rows)
    return _result(asin, 'success', len(rows), path)


def _read_asins(path: Path) -> list[str]:
    if not path.is_file():
        raise FileNotFoundError(f'ASIN \u6587\u4ef6\u4e0d\u5b58\u5728\uff1a{path}')
    values = [line.strip() for line in path.read_text(encoding='utf-8-sig').splitlines()]
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else SCRIPT_DIR / path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='\u91c7\u96c6 SellerSprite \u4e09\u9879\u5386\u53f2\u6307\u6807\u3002')
    parser.add_argument('--asin', action='append', help='\u6307\u5b9a ASIN\uff0c\u53ef\u91cd\u590d\uff1b\u6307\u5b9a\u540e\u4e0d\u8bfb\u53d6 --asin-file\u3002')
    parser.add_argument('--asin-file', default=str(ASIN_PATH), help='ASIN \u6587\u4ef6\uff0c\u9ed8\u8ba4 Amazon/data/ASIN.txt\u3002')
    parser.add_argument('--limit', type=int, default=None, help='\u6700\u591a\u5904\u7406\u524d N \u4e2a ASIN\u3002')
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 0:
        parser.error('--limit \u5fc5\u987b\u662f\u5927\u4e8e\u7b49\u4e8e 0 \u7684\u6574\u6570')

    try:
        asins = [value.strip() for value in args.asin if value and value.strip()] if args.asin else _read_asins(_resolve_path(args.asin_file))
    except (OSError, ValueError) as error:
        print(f'\u8bfb\u53d6 ASIN \u5931\u8d25\uff1a{error}', flush=True)
        return 1
    if args.limit is not None:
        asins = asins[:args.limit]
    if not asins:
        print('\u6ca1\u6709\u53ef\u91c7\u96c6\u7684 ASIN\u3002', flush=True)
        return 1

    metrics: list[tuple[str, Callable[[str], dict[str, Any]]]] = [
        ('\u6708\u9500\u91cf', get_monthly_sales),
        ('\u7559\u8bc4\u7387', get_reviews),
        ('Keepa', get_keepa),
    ]
    exit_code = 0
    for asin in asins:
        print(f'\u5f00\u59cb\u91c7\u96c6 {asin} \u7684\u6570\u636e...', flush=True)
        complete = True
        fatal_access = False
        for label, function in metrics:
            try:
                result = function(asin)
                status = result.get('status', 'unknown')
                rows = result.get('rows', 0)
                print(f'[{asin}] {label}: {status}\uff08{rows} \u884c\uff09', flush=True)
                if status not in {'success', 'skipped'}:
                    complete = False
                    exit_code = max(exit_code, 1)
            except AuthenticationError as error:
                print(f'[{asin}] {label}: \u9274\u6743\u5931\u8d25\uff08HTTP {error.status}\uff09\uff1b\u505c\u6b62\u8be5 ASIN\uff1b\u8bf7\u66f4\u65b0 Cookie\u3002', flush=True)
                complete = False
                exit_code = max(exit_code, 2)
                fatal_access = True
                break
            except CookieError as error:
                print(f'[{asin}] {label}: {error}', flush=True)
                complete = False
                exit_code = max(exit_code, 2)
                fatal_access = True
                break
            except Exception as error:
                print(f'[{asin}] {label}: \u5931\u8d25\uff08{type(error).__name__}\uff09\uff0c\u7ee7\u7eed\u5176\u5b83\u6307\u6807\u3002', flush=True)
                complete = False
                exit_code = max(exit_code, 1)
        if complete:
            print(f'{asin} \u91c7\u96c6\u5b8c\u6210\u3002', flush=True)
        else:
            print(f'{asin} \u672a\u5b8c\u6574\u5b8c\u6210\uff1b\u8bf7\u6839\u636e\u4e0a\u9762\u72b6\u6001\u5904\u7406\u3002', flush=True)
        if fatal_access:
            print('\u68c0\u6d4b\u5230\u9274\u6743\u6216 Cookie \u95ee\u9898\uff0c\u505c\u6b62\u5269\u4f59 ASIN\u3002', flush=True)
            return exit_code
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
