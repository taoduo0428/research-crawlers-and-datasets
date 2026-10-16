import csv
import os
import requests
import pandas as pd

# 加载视频数据
video_data_path = "C:/Users/48842/Desktop/merged_unique.csv"
video_data = pd.read_csv(video_data_path, encoding='GBK')

# 设置请求头部，注意替换成你的cookie信息
headers = {
    'authority': 'api.bilibili.com',
    'accept': 'application/json, text/plain, */*',
    'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
    'origin': 'https://www.bilibili.com',
    'referer': 'https://www.bilibili.com/',
    'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'cookie':"buvid3=5469CAA4-3327-6817-C110-63EC4C48A2B841595infoc; b_nut=1690905941; i-wanna-go-back=-1; _uuid=D12D3DD8-A212-6C5D-B11C-B981056FBDBAC42037infoc; FEED_LIVE_VERSION=V8; DedeUserID=631081975; DedeUserID__ckMd5=8480d8688fb0d91c; buvid4=F5C72193-655E-A103-CF78-0D472A9B9E7042640-023080200-S1vCeaCWwH6m%2B4KHu4XUBQ%3D%3D; hit-new-style-dyn=1; hit-dyn-v2=1; header_theme_version=CLOSE; rpdid=|(kmJYkYmJJJ0J'uYmuJmJkY); b_ut=5; enable_web_push=DISABLE; buvid_fp_plain=undefined; home_feed_column=5; share_source_origin=QQ; bsource=share_source_qqchat; CURRENT_BLACKGAP=0; LIVE_BUVID=AUTO6117049893161979; CURRENT_FNVAL=4048; CURRENT_QUALITY=120; bp_article_offset_631081975=889451606371729475; PVID=1; SESSDATA=6c869d6c%2C1721495951%2C9d302%2A12CjCRufYvkS15bOoxSJo-fMre4_QoE7qs5fhljlEfkb3LlGQx5R5BsiaaczF3CF9f74cSVldLU20xTWpBcTdtbklwcVdla3N2V3lQUjdibVk3Nm9tUFNxUXpfS19oakw3QlVGWTNjaGVYS0pCOC1OcGt5UjVDRWFrLW5mWV9MNFQ4THVnREJMdWFRIIEC; bili_jct=a139dc8644f5d8b9a404d2a8a78167c4; sid=8c7kgsao; browser_resolution=1476-912; bili_ticket=eyJhbGciOiJIUzI1NiIsImtpZCI6InMwMyIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3MDYyOTk5MjUsImlhdCI6MTcwNjA0MDY2NSwicGx0IjotMX0.r16SVxki1OFPhGvUJsf9ohAfIeF_lrf7cnTFjK05CZ8; bili_ticket_expires=1706299865; fingerprint=1aa51aa8bfdd059b0da3ef5be7e57809; buvid_fp=1aa51aa8bfdd059b0da3ef5be7e57809; bp_video_offset_631081975=890470857904750642; b_lsid=F585792B_18D3F86168A"
}

# 获取视频的基本信息
def bili_info(bvid):
    params = {'bvid': bvid}
    response = requests.get('https://api.bilibili.com/x/web-interface/view', headers=headers, params=params)
    return response.json().get('data')

# 获取视频的字幕列表
def bili_subtitle_list(bvid, cid):
    url = f'https://api.bilibili.com/x/player/v2?bvid={bvid}&cid={cid}'
    response = requests.get(url, headers=headers)
    if response.json().get('code') == 0:  # 成功响应
        subtitles = response.json().get('data', {}).get('subtitle', {}).get('subtitles', [])
        return ['https:' + x['subtitle_url'] for x in subtitles]
    return []

# 下载字幕
def download_subtitle(url):
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.json().get('body')
    return None

# 确定输出文件夹
output_folder = "C:\\Users\\48842\\Desktop\\B站爬虫"
os.makedirs(output_folder, exist_ok=True)  # 创建文件夹如果它不存在
output_file_path = os.path.join(output_folder, 'subtitles_BV_CID.csv')  # 输出文件的完整路径

# 准备写入新的CSV文件
with open(output_file_path, 'w', newline='', encoding='utf-8') as csvfile:
    writer = csv.writer(csvfile)
    writer.writerow(['字幕', 'BV号', 'CID'])  # 写入表头

    # 逐行处理视频数据
    for index, row in video_data.iterrows():
        bvid = row['BV号']
        cid = row['CID']
        video_info = bili_info(bvid)

        # 如果成功获取到了视频信息，则尝试获取字幕
        if video_info:
            subtitles_urls = bili_subtitle_list(bvid, cid)
            if subtitles_urls:
                subtitle_text = download_subtitle(subtitles_urls[0])
                if subtitle_text:
                    # 移除字幕文本中的换行符和双引号以免破坏CSV格式
                    subtitle_text = subtitle_text.replace('\n', ' ').replace('"', "'")
                    writer.writerow([subtitle_text, bvid, cid])  # 写入字幕文本、BV号和CID
