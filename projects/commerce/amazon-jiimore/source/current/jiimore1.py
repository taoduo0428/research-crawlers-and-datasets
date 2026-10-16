import requests
import json
import os
import pandas as pd

# 配置请求头和代理
headers = {
    "Origin": "https://www.jiimore.com",
    "Referer": "https://www.jiimore.com/data/product",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Accept": "application/json",
    "Content-Type": "application/json",
}
proxies = {
    'http': '127.0.0.1:7890',
    'https': '127.0.0.1:7890',
}

# 登录函数，用于获取会话
def login(phone_num, password):
    session = requests.Session()
    session.proxies.update(proxies)
    session.headers.update(headers)
    url = "https://www.jiimore.com/api/basic/basic/wechat/offiaccount/qrcode"
    response = session.post(url)
    sid = response.json()['cookies']['sid']
    session.headers['sid'] = sid
    data = json.dumps({"countryCode": "US", "mobile": phone_num, "pwd": password}, separators=(',', ':'))
    url = "https://www.jiimore.com/api/basic/basic/mobile/login"
    response = session.post(url, data=data)
    if response.json().get('uname'):
        print('登录成功')
    else:
        print('登录失败，请检查凭证')
    return session

# 获取类目ID
def get_category_id(session, name):
    url = "https://www.jiimore.com/api/amz-aba/aba/basCategory/list"
    data = json.dumps({"countryCode": "US", "name": name}, separators=(',', ':'))
    response = session.post(url, data=data)
    response_data = response.json()
    if response_data and isinstance(response_data, list) and 'categoryId' in response_data[0]:
        return response_data[0]['categoryId']
    else:
        print("未找到类目ID: ", response_data)
        return None

# 获取商品数据
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

def format_date(date):
    """
    将日期从YYYYMMDD格式转换为YYYY-MM-DD格式
    :param date: 字符串格式的日期，例如 '20210401'
    :return: 格式化后的日期字符串，例如 '2021-04-01'
    """
    if isinstance(date, int):
        date = str(date)  # 转换为字符串，如果是整数
    if len(date) == 8:
        return f"{date[:4]}-{date[4:6]}-{date[6:]}"
    return date  # 如果格式不匹配，返回原字符串


def get_click_data(session, asin):
    """获取指定ASIN的点击量数据"""
    url = f"https://www.jiimore.com/api/amz-aba/aba/opportunityExplorer/asin/{asin}/ATVPDKIKX0DER/week"
    response = session.post(url, headers=headers, proxies=proxies)
    click_data = response.json().get('trends', [])
    return click_data


# 主函数，执行所有任务
def main(keyword, phone_num, password):
    session = login(phone_num, password)
    category_id = get_category_id(session, keyword)
    if not category_id:
        print("Failed to find category ID for:", keyword)
        return

    # Ensure directories exist
    os.makedirs('data/jiimore', exist_ok=True)
    jiimore_path = 'data/jiimore/jiimore.csv'
    asin_path = 'data/jiimore/asin.csv'
    asin_txt_path = 'data/ASIN.txt'

    # Initialize or read existing ASINs
    if os.path.exists(asin_path):
        existing_asins = set(pd.read_csv(asin_path)['ASIN'])
    else:
        existing_asins = set()

    all_data_jiimore = []
    all_data_asin = []

    # Fetch data
    for page in range(1, 5):  # Assuming there are 4 pages to fetch
        data_list = get_data(session, category_id, page)
        for data in data_list:
            asin = data.get('asin')
            if asin not in existing_asins:
                existing_asins.add(asin)
                click_data = get_click_data(session, asin)  # Fetch weekly click data

                # Process each ASIN's click data
                for click_detail in click_data:
                    item_jiimore = {
                        'ASIN': asin,
                        '上架时间': format_date(data.get('launchDate')[:8]),
                        'Amazon_url': f'https://www.amazon.com/dp/{asin}?language=en_US',
                        '周点击量': click_detail.get('clickCountT7', '无数据'),
                        '日期': format_date(click_detail.get('day', '无数据'))
                    }
                    all_data_jiimore.append(item_jiimore)

                item_asin = {
                    'ASIN': asin,
                    '上架时间': format_date(data.get('launchDate')[:8]),
                    'Amazon_url': f'https://www.amazon.com/dp/{asin}?language=en_US'
                }
                all_data_asin.append(item_asin)

                # Save ASIN to text file
                with open(asin_txt_path, 'a') as f:
                    f.write(asin + '\n')

    # Save data to CSV files
    if all_data_asin:
        pd.DataFrame(all_data_asin).to_csv(asin_path, mode='a', header=not os.path.exists(asin_path), index=False)
    if all_data_jiimore:
        pd.DataFrame(all_data_jiimore).to_csv(jiimore_path, mode='a', header=not os.path.exists(jiimore_path), index=False)

    print(f'数据采集完成: {keyword}')





if __name__ == '__main__':
    main('Makeup', os.environ.get('JIIMORE_PHONE', ''), os.environ.get('JIIMORE_PASSWORD', ''))
