import json
import os
import time
from collections import defaultdict
from datetime import datetime
from functools import wraps

import pandas as pd
import requests

headers = {
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
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0"
}
cookies = {
    "rank-login-user": os.environ.get('SELLERSPRITE_COOKIE', ''),
}
proxies = {
    'http': '127.0.0.1:7890',
    'https': '127.0.0.1:7890',
}


def retry(retries=3, delay=1):
    """
    装饰器，捕获异常并重试指定次数
    :param retries: 重试次数
    :param delay: 重试间隔（秒）
    """
    def wrapper(func):
        @wraps(func)
        def decorated_func(*args, **kwargs):
            for attempt in range(retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt < retries:
                        print(f"Attempt {attempt + 1} failed: {e}, retrying after {delay} seconds...")
                        time.sleep(delay)
                    else:
                        raise
        return decorated_func

    return wrapper


@retry()
def get_monthly_sales(asin):
    """
    获取月销量数据
    :param asin: ASIN号
    :return:
    """
    print(f'正在采集 {asin} 的月销量数据...')
    path = 'data/sellersprite/monthly_sales.csv'
    if not os.path.exists(path):
        pd.DataFrame(columns=['ASIN', '月份', '月销量', '月销售额', '平均单价']).to_csv(path, index=False)
    # 读取CSV文件到DataFrame
    df = pd.read_csv(path)
    # 检查数据是否已存在
    if (df['ASIN'] == asin).any():
        print("数据已存在，无需添加。")
        return
    url = "https://www.sellersprite.com/v2/competitor-lookup/chart-monthly.json"
    data = {
        "asin": asin,
        "marketId": "1"
    }
    response = requests.post(url, headers=headers, cookies=cookies, data=data, proxies=proxies)
    #print(response.text)
    datas = response.json()['data']['chartData']
    item_list = []
    if not datas:
        item = {
            'ASIN': asin,
            '月份': '',
            '月销量': '',
            '月销售额': '',
            '平均单价': '',
        }
        item_list.append(item)
    for key, value in datas.items():
        item = {}
        item['ASIN'] = asin
        item['月份'] = key
        item['月销量'] = value.get('totalUnits', '')
        item['月销售额'] = value.get('totalAmount', '')
        item['平均单价'] = value.get('averagePrice', '')
        print(item)
        item_list.append(item)
    df = pd.DataFrame(item_list)
    df.to_csv(path, index=False, mode='a', header=False)
    time.sleep(0.3)


@retry()
def get_reviews(asin):
    """
    获取留评率数据
    :param asin: ASIN号
    :return:
    """
    print(f'正在采集 {asin} 的留评率数据...')
    path = 'data/sellersprite/reviews.csv'
    if not os.path.exists(path):
        pd.DataFrame(columns=['ASIN', '月份', '评分数增长值', '留评率']).to_csv(path, index=False)
    # 读取CSV文件到DataFrame
    df = pd.read_csv(path)
    # 检查数据是否已存在
    if (df['ASIN'] == asin).any():
        print("数据已存在，无需添加。")
        return
    url = f"https://www.sellersprite.com/v3/api/trends/reviews/1/{asin}"
    data = {}
    data = json.dumps(data, separators=(',', ':'))
    response = requests.post(url, headers=headers, cookies=cookies, data=data, proxies=proxies)
    datas = response.json()['data']
    dalta = datas['dalta']
    dalta_month = datas['daltaMonth']
    rate = datas['rate']
    item_list = []
    for i in range(len(dalta_month)):
        item = {}
        item['ASIN'] = asin
        item['月份'] = dalta_month[i][:4]+'-'+dalta_month[i][4:]
        item['评分数增长值'] = dalta[i]
        item['留评率'] = rate[i]
        print(item)
        item_list.append(item)
    df = pd.DataFrame(item_list)
    df.to_csv(path, index=False, mode='a', header=False)
    time.sleep(0.3)


def date_index(time_list):
    # 将时间字符串转换为datetime对象并按日期分类
    time_dict = defaultdict(list)
    for idx, t in enumerate(time_list):
        dt = datetime.strptime(t, '%Y-%m-%d %H:%M')
        date_key = dt.date()
        time_dict[date_key].append((dt, idx))
    # 获取每一天最新时间的索引
    latest_time_index = [max(times, key=lambda x: x[0])[1] for times in time_dict.values()]
    return latest_time_index


@retry()
def get_keepa(asin):
    """
    获取历史趋势数据
    :param asin: ASIN号
    :return:
    """
    print(f'正在采集 {asin} 的历史趋势数据...')
    path = 'data/sellersprite/keepa.csv'
    if not os.path.exists(path):
        pd.DataFrame(columns=['ASIN', '日期', '评分', '评分数', '价格']).to_csv(path, index=False)
    # 读取CSV文件到DataFrame
    df = pd.read_csv(path)
    # 检查数据是否已存在
    if (df['ASIN'] == asin).any():
        print("数据已存在，无需添加。")
        return
    headers['accept'] = "application/json, text/plain, */*"
    url = f"https://www.sellersprite.com/v2/keepa/subTrend/us/{asin}"
    params = {
        "parentModule": "ARA"
    }
    response = requests.get(url, headers=headers, cookies=cookies, params=params, proxies=proxies)
    datas = response.json()['data']['keepa']
    times = datas['times']
    keepa = datas['keepa']
    latest_time_index = date_index(times)
    item_list = []
    for i in latest_time_index:
        item = {}
        item['ASIN'] = asin
        item['日期'] = times[i].split(' ')[0]
        item['评分'] = keepa['rating'][i]
        item['评分数'] = keepa['reviews'][i]
        item['价格'] = keepa['price'][i]
        print(item)
        item_list.append(item)
    df = pd.DataFrame(item_list)
    df.to_csv(path, index=False, mode='a', header=False)
    time.sleep(0.3)


def main(asin):
    print(f'开始采集 {asin} 的数据...')
    # 获取月销量数据
    get_monthly_sales(asin)
    # 获取留评率数据
    get_reviews(asin)
    # 获取历史趋势
    get_keepa(asin)
    print(f'{asin} 采集完成')
    time.sleep(0.5)


if __name__ == '__main__':
    # 读取asin文件，获取所有asin
    with open('data/ASIN.txt', 'r', encoding='utf-8') as f:
        asin_list = f.read().split('\n')
    # 遍历asin进行爬取
    for asin in asin_list:
        main(asin)
