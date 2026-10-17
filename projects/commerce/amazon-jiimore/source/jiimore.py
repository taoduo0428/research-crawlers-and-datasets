import argparse
import json
import time
import tempfile
import os
import requests
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from pathlib import Path
from project_config import DATA_ROOT, get_proxies, load_config

headers = {
    "Origin": "https://www.jiimore.com",
    "Referer": "https://www.jiimore.com/data/product",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "accept": "application/json",
    "content-type": "application/json",
}




DATA_DIR = DATA_ROOT / 'jiimore'
FIELDS = ['ASIN', '上架时间', 'Amazon_url', '周点击量', '日期']


class AccessRequired(RuntimeError):
    pass


def _post(session, url, **kwargs):
    config = load_config()
    response = session.post(url, headers=headers, proxies=get_proxies(config),
                            timeout=config['request_timeout'], **kwargs)
    if response.status_code in (401, 403, 407, 418, 429):
        raise AccessRequired(f'极目登录或访问受限（HTTP {response.status_code}），请检查账号或稍后重试。')
    if response.status_code >= 400:
        raise RuntimeError(f'极目接口请求失败（HTTP {response.status_code}）')
    return response


def retry(retries=3, delay=1):
    def wrapper(func):
        @wraps(func)
        def decorated_func(*args, **kwargs):
            attempts = 0
            while attempts <= retries:
                try:
                    return func(*args, **kwargs)
                except AccessRequired:
                    raise
                except Exception as e:
                    attempts += 1
                    if attempts > retries:
                        raise
                    print(f"Attempt {attempts} failed: {e}. Retrying after {delay} seconds...")
                    time.sleep(delay)
        return decorated_func
    return wrapper

@retry()
def login(session, phone_num, password):
    url = "https://www.jiimore.com/api/basic/basic/wechat/offiaccount/qrcode"
    response = _post(session, url)
    sid = response.json()['cookies']['sid']
    headers['sid'] = sid
    url = "https://www.jiimore.com/api/basic/basic/mobile/login"
    data = json.dumps({"countryCode": "US", "mobile": phone_num, "pwd": password}, separators=(',', ':'))
    response = _post(session, url, data=data)
    if not response.json().get('uname'):
        raise AccessRequired('极目登录未成功，请检查本地账号配置。')
    print('登录成功')
    return session

def get_data(session, category_id, page):
    url = "https://www.jiimore.com/api/amz-aba/aba/opportunityExplorer/nicheAsin/page"
    params = {"r": "asinPage"}
    data = json.dumps({
        "countryCode": "US",
        "grade": "week",
        "page": page,
        "pageSize": 500,
        "categoryIds": [category_id],
        "marketplaceId": "ATVPDKIKX0DER"
    }, separators=(',', ':'))
    response = _post(session, url, params=params, data=data)
    return response.json().get('data', [])
@retry()
def get_category_id(session, name):
    """
    获取类目ID
    :param session: 已登录的会话
    :param name: 类目名称
    :return: 类目ID 或 None
    """
    url = "https://www.jiimore.com/api/amz-aba/aba/basCategory/list"
    data = json.dumps({
        "countryCode": "US",
        "name": name
    }, separators=(',', ':'))
    response = _post(session, url, data=data)
    datas = response.json()
    if datas and isinstance(datas, list) and 'categoryId' in datas[0]:
        return datas[0]['categoryId']
    return None

def format_date(date):
    """
    将日期从YYYYMMDD格式转换为YYYY-MM-DD格式
    :param date: 字符串格式的日期，例如 '20210401'
    :return: 格式化后的日期字符串，例如 '2021-04-01'
    """
    if len(date) == 8:
        return f"{date[:4]}-{date[4:6]}-{date[6:]}"
    return date  # 返回原字符串如果格式不匹配


def merge_asins(asins, path=None):
    """Keep the existing list intact and atomically append unseen ASINs."""
    path = Path(path) if path is not None else DATA_ROOT / 'ASIN.txt'
    existed = path.exists()
    before = path.read_bytes() if existed else b''
    known = {line.strip().upper() for line in before.decode('utf-8-sig').splitlines() if line.strip()}
    additions = []
    for value in asins:
        if value is None or pd.isna(value):
            continue
        asin = str(value).strip().upper()
        if asin and asin not in known:
            known.add(asin)
            additions.append(asin)
    if existed and not additions:
        return 0
    newline = b'\r\n' if b'\r\n' in before else b'\n'
    content = before
    if additions:
        if content and not content.endswith((b'\n', b'\r')):
            content += newline
        content += newline.join(value.encode('utf-8') for value in additions) + newline
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('wb', dir=path.parent, prefix=path.name + '.', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists() != existed or (existed and path.read_bytes() != before):
            raise RuntimeError('ASIN 清单在更新期间发生变化，本次未写入，请重新运行。')
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return len(additions)


def main(keyword='Makeup', max_pages=4, export_asins=False):
    if not 1 <= max_pages <= 1000:
        raise ValueError('max_pages 必须在 1 到 1000 之间。')
    config = load_config()
    phone_num = config['jiimore']['phone_num'].strip()
    password = config['jiimore']['password']
    if not phone_num or not password:
        raise AccessRequired('请在 config.local.json 的 jiimore 中配置 phone_num 和 password。')
    session = requests.Session()
    session.trust_env = False
    try:
        session = login(session, phone_num, password)
        category_id = get_category_id(session, keyword)
        if not category_id:
            raise ValueError('未找到对应的极目类目。')
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = DATA_DIR / 'jiimore.csv'
        existing = pd.read_csv(path, dtype=str).fillna('') if path.exists() else pd.DataFrame(columns=FIELDS)
        existing_asins = set(existing['ASIN'])
        additions = []
        with ThreadPoolExecutor(max_workers=min(5, max_pages)) as executor:
            futures = [executor.submit(get_data, session, category_id, page) for page in range(1, max_pages + 1)]
            for future in futures:
                for data in future.result() or []:
                    asin = str(data.get('asin') or '').strip()
                    if not asin or asin in existing_asins:
                        continue
                    existing_asins.add(asin)
                    launch_date = str(data.get('launchDate') or '')
                    additions.append({
                        'ASIN': asin,
                        '上架时间': format_date(launch_date[:8]),
                        'Amazon_url': f'https://www.amazon.com/dp/{asin}?language=en_US',
                        '周点击量': '',
                        '日期': '',
                    })
        if additions or not path.exists():
            combined = pd.concat([existing, pd.DataFrame(additions, columns=FIELDS)], ignore_index=True)
            temporary = path.with_name(path.name + '.tmp')
            try:
                combined.to_csv(temporary, columns=FIELDS, index=False)
                temporary.replace(path)
            finally:
                if temporary.exists():
                    temporary.unlink()
        # export_asins remains accepted for old callers; every successful run
        # now merges the saved ASINs without replacing existing input entries.
        asins = pd.read_csv(path, dtype=str)['ASIN'].dropna().tolist()
        asin_added = merge_asins(asins)
        print(f'ASIN 清单已更新，新增 {asin_added} 个：{DATA_ROOT / "ASIN.txt"}')
        print(f'结果目录：{DATA_DIR}')
        print(f'{keyword} 本次新增 {len(additions)} 个商品。')
        return len(additions)
    finally:
        session.close()


def cli(argv=None):
    parser = argparse.ArgumentParser(description='极目商品数据采集')
    parser.add_argument('--keyword', default='Makeup', help='类目关键词')
    parser.add_argument('--max-pages', type=int, default=4, help='最多采集页数，默认 4')
    parser.add_argument('--export-asins', action='store_true', help='兼容旧命令；现在每次成功采集后都会自动去重追加 ASIN.txt')
    args = parser.parse_args(argv)
    try:
        main(args.keyword, args.max_pages, args.export_asins)
    except (AccessRequired, ValueError):
        print('极目采集未完成：请检查 config.local.json 中的账号、密码及页数设置。')
        return 2
    except Exception as error:
        print(f'极目采集失败：{type(error).__name__}，已有文件保留。')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(cli())
