import pandas as pd
import requests
import json

import requests

headers = {
    'authority': 'api.bilibili.com',
    'accept': '*/*',
    'accept-language': 'en',
    'sec-ch-ua': '"Not/A)Brand";v="99", "Google Chrome";v="115", "Chromium";v="115"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
    'sec-fetch-dest': 'script',
    'sec-fetch-mode': 'no-cors',
    'sec-fetch-site': 'cross-site',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
}

params = {
    'jsonp': 'jsonp',
    'bvid': 'BV1EP411n7Nd',
    'callback': 'jQuery111209852126968317105_1691925126049',
    '_': '1691925126050',
}

response = requests.get('https://api.bilibili.com/x/web-interface/view', params=params, headers=headers)
response.encoding='utf-8'

url_p= response.text.split("\"pic\":\"")[1].split("\"")[0]
# text = json.loads(response.text)
# text = json.loads(response.text).get('data').get('pages').get('pic')

print(url_p)

import os

def download_image(url, filename):
    try:
        response = requests.get(url)
        response.raise_for_status()

        with open(filename, 'wb') as file:
            file.write(response.content)

        print("图片已成功下载并保存到：", filename)
    except requests.exceptions.RequestException as e:
        print("下载图片时发生错误：", e)

# 图片链接和文件名
image_url = url_p  # 图片链接
filename = "1.jpg"  # 文件名

# 获取当前工作目录并拼接文件名
current_directory = os.getcwd()
file_path = os.path.join(current_directory, filename)

download_image(image_url, file_path)
