import json
import os
import requests
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
from functools import wraps

headers = {
    "Origin": "https://www.jiimore.com",
    "Referer": "https://www.jiimore.com/data/product",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "accept": "application/json",
    "content-type": "application/json",
}
proxies = {
    'http': '127.0.0.1:7890',
    'https': '127.0.0.1:7890',
}



def retry(retries=3, delay=1):
    def wrapper(func):
        @wraps(func)
        def decorated_func(*args, **kwargs):
            attempts = 0
            while attempts <= retries:
                try:
                    return func(*args, **kwargs)
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
    response = session.post(url, headers=headers, proxies=proxies)
    sid = response.json()['cookies']['sid']
    headers['sid'] = sid
    url = "https://www.jiimore.com/api/basic/basic/mobile/login"
    data = json.dumps({"countryCode": "US", "mobile": phone_num, "pwd": password}, separators=(',', ':'))
    response = session.post(url, headers=headers, data=data, proxies=proxies)
    if response.json().get('uname'):
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
    response = session.post(url, headers=headers, params=params, data=data, proxies=proxies)
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
    response = session.post(url, headers=headers, data=data, proxies=proxies)
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


def main(keyword):
    session = requests.Session()
    phone_num = os.environ.get('JIIMORE_PHONE', '')
    password = os.environ.get('JIIMORE_PASSWORD', '')
    session = login(session, phone_num, password)
    category_id = get_category_id(session, keyword)
    if not category_id:
        print("Failed to find category ID for:", keyword)
        return

    jiimore_path = 'data/jiimore/jiimore.csv'
    if not os.path.exists(jiimore_path):
        pd.DataFrame(columns=['ASIN', '上架时间', 'Amazon_url', '周点击量', '日期']).to_csv(jiimore_path, index=False)

    existing_asins = set(pd.read_csv(jiimore_path)['ASIN']) if os.path.exists(jiimore_path) else set()
    all_data = []

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(get_data, session, category_id, page) for page in range(1, 5)]
        for future in futures:
            data_list = future.result()
            if data_list:
                for data in data_list:
                    asin = data.get('asin')
                    if asin not in existing_asins:
                        existing_asins.add(asin)
                        all_data.append({
                            'ASIN': asin,
                            '上架时间': format_date(data.get('launchDate')[:8]),
                            'Amazon_url': f'https://www.amazon.com/dp/{asin}?language=en_US',
                            '周点击量': 'To be collected',  # Placeholder for click data
                            '日期': 'To be collected'  # Placeholder for date data
                        })
    if all_data:
        pd.DataFrame(all_data).to_csv(jiimore_path, mode='a', header=False, index=False)
    print(f'{keyword} data collection complete.')

if __name__ == '__main__':
    main("Makeup")
