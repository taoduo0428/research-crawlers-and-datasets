import argparse
import csv
import json
import os
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from DrissionPage import ChromiumOptions, ChromiumPage

try:
    from .amazon_extract import (
        PRODUCT_FIELDS, REVIEW_FIELDS, extract_product, extract_reviews,
        find_review_url, find_next_review_url, page_status, extract_customer_tags,
    )
except ImportError:  # 直接运行 Amazon/amazon.py 时使用同目录模块。
    from amazon_extract import (
        PRODUCT_FIELDS, REVIEW_FIELDS, extract_product, extract_reviews,
        find_review_url, find_next_review_url, page_status, extract_customer_tags,
    )


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
DATA_DIR = SCRIPT_DIR / 'data' / 'amazon'
EDGE_DEBUG_PORT = 9223
EDGE_PROFILE_DIR = SCRIPT_DIR / '.edge-amazon-profile'
MAX_REVIEW_PAGES = 1
AMAZON_DOMAIN = 'com'
AMAZON_LANGUAGE = 'en_US'
DEFAULT_ASINS = ['B00FLYWNYQ', 'B09XMYFTB7']
TAG_FIELDS = ['ASIN', '标签', '标签属性', 'customers_mention', 'positive', 'negative', '标签内容']
page = None


class AccessRequired(RuntimeError):
    def __init__(self, status):
        self.status = status
        self.result = None
        self.reviews_seen = 0
        self.reviews_added = 0
        super().__init__(status)


def setup_browser():
    edge_path = shutil.which('msedge')
    if not edge_path:
        for program_dir in (os.environ.get('PROGRAMFILES(X86)'), os.environ.get('PROGRAMFILES'), os.environ.get('LOCALAPPDATA')):
            if program_dir:
                candidate = Path(program_dir) / 'Microsoft' / 'Edge' / 'Application' / 'msedge.exe'
                if candidate.is_file():
                    edge_path = str(candidate)
                    break
    if not edge_path:
        raise FileNotFoundError('未找到 Microsoft Edge，请先安装 Edge。')
    co = ChromiumOptions(read_file=False)
    co.set_browser_path(edge_path)
    co.set_local_port(EDGE_DEBUG_PORT)
    co.set_user_data_path(str(EDGE_PROFILE_DIR))
    co.incognito(False)
    co.no_imgs()
    co.set_timeouts(base=10, page_load=30, script=15)
    return ChromiumPage(co)


def read_rows(path):
    path = Path(path)
    if not path.exists():
        return []
    with path.open('r', encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def atomic_csv(path, fields, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', newline='', dir=path.parent, prefix=path.name + '.', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
            writer.writeheader()
            writer.writerows({key: row.get(key, '') for key in fields} for row in rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def save_product(item, path=None):
    if not str(item.get('ASIN', '')).strip() or not str(item.get('标题', '')).strip():
        raise ValueError('商品标题尚未加载，不保存空商品记录。')
    path = Path(path) if path is not None else DATA_DIR / 'goods_infos.csv'
    rows = read_rows(path)
    matches = [index for index, row in enumerate(rows) if row.get('ASIN') == item['ASIN']]
    if matches:
        index = matches[0]
        updated = dict(rows[index])
        # 新页面缺少可选字段时保留已经采到的值。
        updated.update({key: value for key, value in item.items() if key in PRODUCT_FIELDS and value is not None and str(value).strip()})
        if updated == rows[index]:
            return False
        rows[index] = updated
    else:
        rows.append(item)
    atomic_csv(path, PRODUCT_FIELDS, rows)
    return True


def row_key(row, fields):
    return tuple(' '.join(str(row.get(key) or '').split()) for key in fields)


def append_unique(path, fields, items):
    rows = read_rows(path)
    known = {row_key(row, fields) for row in rows}
    additions = []
    for item in items:
        key = row_key(item, fields)
        if key not in known:
            known.add(key)
            additions.append(item)
    if additions:
        atomic_csv(path, fields, rows + additions)
    return len(additions)


def save_reviews(items, path=None):
    valid = [item for item in items if str(item.get('ASIN') or '').strip() and str(item.get('评论内容') or '').strip()]
    return append_unique(Path(path) if path is not None else DATA_DIR / 'reviews.csv', REVIEW_FIELDS, valid)


def wait_for_content(kind, asin, url, timeout=20):
    deadline = time.monotonic() + timeout
    latest = ''
    while True:
        latest = page.html or ''
        status = page_status(latest, page.url)
        if status != 'ready':
            raise AccessRequired(status)
        if kind == 'product':
            if extract_product(latest, asin, url).get('标题'):
                # 标题出现后给价格等动态字段一个短暂加载时间。
                time.sleep(1)
                latest = page.html or latest
                status = page_status(latest, page.url)
                if status != 'ready':
                    raise AccessRequired(status)
                return latest
        elif extract_reviews(latest, asin):
            return latest
        if time.monotonic() >= deadline:
            return latest
        time.sleep(0.5)


def get_reviews(asin, max_pages=MAX_REVIEW_PAGES, product_html=''):
    base_url = f'https://www.amazon.{AMAZON_DOMAIN}'
    review_url = find_review_url(product_html, asin, base_url)
    if not review_url:
        review_url = f'{base_url}/product-reviews/{asin}/?' + urlencode({'reviewerType': 'all_reviews', 'language': AMAZON_LANGUAGE, 'pageNumber': 1})
    print(f'开始爬取 {asin} 的评论，最多 {max_pages} 页...', flush=True)
    added = 0
    seen = 0
    visited = set()
    for page_num in range(1, max_pages + 1):
        if review_url in visited:
            break
        visited.add(review_url)
        page.get(review_url)
        try:
            html = wait_for_content('reviews', asin, review_url)
        except AccessRequired as error:
            error.reviews_seen, error.reviews_added = seen, added
            raise
        if page_num == 1:
            # 保留原有的页面翻译能力；没有按钮时直接采集原文。
            translate = page.ele('css:span.a-button.cr-see-translated-reviews', timeout=0.3)
            if not translate:
                translate = page.ele('text:将所有评论翻译成中文', timeout=0.3)
            if translate:
                try:
                    translate.click()
                    html = wait_for_content('reviews', asin, review_url)
                except AccessRequired:
                    raise
                except Exception:
                    print('评论翻译按钮未完成操作，继续提取页面现有正文。', flush=True)
        items = extract_reviews(html, asin)
        if not items:
            print(f'{asin} 第 {page_num} 页未取得有效评论正文，本次不写入空评论。', flush=True)
            return {'status': 'empty', 'seen': seen, 'added': added}
        seen += len(items)
        new_count = save_reviews(items)
        added += new_count
        print(f'{asin} 第 {page_num} 页读取 {len(items)} 条，新增保存 {new_count} 条。', flush=True)
        if page_num < max_pages:
            next_url = find_next_review_url(html, asin, page.url)
            if not next_url:
                break
            review_url = next_url
    return {'status': 'success' if seen else 'empty', 'seen': seen, 'added': added}


def get_info(asin, max_pages=MAX_REVIEW_PAGES, reviews_only=False):
    result = {'ASIN': asin, 'product': 'skipped' if reviews_only else 'failed', 'reviews': 'pending', 'reviews_seen': 0, 'reviews_added': 0}
    product_html = ''
    if not reviews_only:
        url = f'https://www.amazon.{AMAZON_DOMAIN}/dp/{asin}?' + urlencode({'language': AMAZON_LANGUAGE})
        print(f'正在采集 {asin} 商品数据...', flush=True)
        try:
            page.get(url)
            product_html = wait_for_content('product', asin, url)
            item = extract_product(product_html, asin, url)
            changed = save_product(item)
            tags = extract_customer_tags(product_html, asin)
            if tags:
                append_unique(DATA_DIR / 'customers_say_tags.csv', TAG_FIELDS, tags)
            result['product'] = 'saved'
            print(f'{asin} 商品信息有效，' + ('已更新保存。' if changed else '与现有记录一致。'), flush=True)
        except AccessRequired as error:
            result['product'] = error.status
            result['reviews'] = error.status
            error.result = result
            raise
        except Exception as error:
            print(f'{asin} 商品信息未保存：{type(error).__name__}: {str(error)[:180]}', flush=True)
    # 商品已经存在或本次详情不完整，都允许独立补采评论。
    try:
        reviews = get_reviews(asin, max_pages, product_html)
        result.update(reviews=reviews['status'], reviews_seen=reviews['seen'], reviews_added=reviews['added'])
    except AccessRequired as error:
        result.update(reviews=error.status, reviews_seen=error.reviews_seen, reviews_added=error.reviews_added)
        error.result = result
        raise
    except Exception as error:
        result['reviews'] = 'failed'
        print(f'{asin} 评论采集失败：{type(error).__name__}: {str(error)[:180]}', flush=True)
    return result


def write_run_status(results, access_status=None):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    report = {'time': datetime.now().isoformat(timespec='seconds'), 'access_status': access_status, 'results': results}
    path = DATA_DIR / 'last_run.json'
    temporary = path.with_name(path.name + '.tmp')
    try:
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _normalize_asin(value):
    return str(value or '').strip().upper()


def read_asin_file(path):
    path = Path(path)
    if not path.is_absolute():
        path = SCRIPT_DIR / path
    if not path.is_file():
        raise FileNotFoundError(f'ASIN 文件不存在：{path}')
    values = []
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        # 支持一行一个 ASIN，也兼容逗号/空白分隔的清单文件。
        for value in line.replace(',', ' ').split():
            normalized = _normalize_asin(value)
            if normalized and normalized != 'ASIN':
                values.append(normalized)
    return values


def select_asins(asins=None, asin_file=None, limit=None):
    """按显式输入选择 ASIN；没有显式输入时才使用两个默认商品。"""
    if limit is not None and limit < 0:
        raise ValueError('--limit 不能为负数')
    explicit = asins is not None or asin_file is not None
    values = []
    if asins is not None:
        values.extend(_normalize_asin(value) for value in asins)
    if asin_file is not None:
        values.extend(read_asin_file(asin_file))
    if not explicit:
        values = list(DEFAULT_ASINS)
    selected = []
    seen = set()
    for value in values:
        if value and value not in seen:
            selected.append(value)
            seen.add(value)
    return selected if limit is None else selected[:limit]


def main(argv=None):
    global page
    parser = argparse.ArgumentParser(description='采集 Amazon 商品及评论，默认使用两个测试商品。')
    parser.add_argument('--asin', dest='asins', action='append', metavar='ASIN', help='指定一个 ASIN；可重复使用')
    parser.add_argument('--asin-file', type=Path, help='ASIN 清单文件；支持一行一个或逗号/空白分隔')
    parser.add_argument('--limit', type=int, default=None, help='最多采集多少个去重后的 ASIN，0 表示不采集')
    parser.add_argument('--reviews-only', action='store_true', help='只补采评论，保留商品信息')
    parser.add_argument('--max-review-pages', type=int, default=MAX_REVIEW_PAGES)
    args = parser.parse_args(argv)
    if not 1 <= args.max_review_pages <= 20:
        parser.error('--max-review-pages 必须在 1 到 20 之间')
    try:
        asins = select_asins(args.asins, args.asin_file, args.limit)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f'运行脚本：{Path(__file__).resolve()}', flush=True)
    print(f'结果目录：{DATA_DIR}', flush=True)
    if not asins:
        write_run_status([], None)
        print('没有待采集的 ASIN，未启动浏览器。', flush=True)
        return 0
    print(f'本次 ASIN：{", ".join(asins)}', flush=True)
    page = setup_browser()
    results = []
    access_status = None
    for asin in asins:
        try:
            result = get_info(asin, args.max_review_pages, args.reviews_only)
            results.append(result)
        except AccessRequired as error:
            access_status = error.status
            results.append(error.result or {'ASIN': asin, 'product': 'interrupted', 'reviews': error.status, 'reviews_seen': error.reviews_seen, 'reviews_added': error.reviews_added})
            action = '登录 Amazon' if error.status == 'login_required' else '手动完成页面验证'
            print(f'采集暂停：请在当前 Edge 采集窗口{action}，完成后重新运行。已有结果会保留。', flush=True)
            break
    write_run_status(results, access_status)
    goods = sum(item['product'] == 'saved' for item in results)
    comments = sum(item['reviews_seen'] for item in results)
    added = sum(item['reviews_added'] for item in results)
    print(f'本次结束：商品有效 {goods} 个，评论读取 {comments} 条，新增保存 {added} 条。', flush=True)
    print('Edge 采集窗口保持打开，便于检查页面和保留登录。', flush=True)
    if access_status:
        return 2
    return 0 if all(item['reviews'] == 'success' and (args.reviews_only or item['product'] == 'saved') for item in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
