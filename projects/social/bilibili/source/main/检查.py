import pandas as pd
import requests
import os

# 确保路径正确
video_data_path = "C:/Users/48842/Desktop/merged_unique.csv"
try:
    video_data = pd.read_csv(video_data_path, encoding='utf-8')
    print(f"Loaded {len(video_data)} rows from the CSV.")
except UnicodeDecodeError:
    try:
        video_data = pd.read_csv(video_data_path, encoding='GB18030')
        print(f"Loaded {len(video_data)} rows from the CSV using GB18030.")
    except Exception as e:
        print("Error loading CSV file with UTF-8 and GB18030:", e)
        exit()
except Exception as e:
    print("Error loading CSV file:", e)
    exit()

# 更新请求头，尤其是Cookie
headers = {
    'authority': 'api.bilibili.com',
    'accept': 'application/json, text/plain, */*',
    'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
    'origin': 'https://www.bilibili.com',
    'referer': 'https://www.bilibili.com/',
    'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'cookie': "CURRENT_FNVAL=4048; enable_web_push=DISABLE; buvid3=0EBBF7BB-FE14-88F8-E78C-4C732780DCCD71579infoc; b_nut=1706859171; i-wanna-go-back=-1; b_ut=7; _uuid=77556E410-5B1D-A147-C45C-C67631D575BC72835infoc; buvid4=93E3B393-C104-C800-3418-408C939DF4CA73042-024020207-IAAnKFSLv27gVhP1IipP0w%3D%3D; buvid_fp=413224f0c983e4366668e0885b95863f; rpdid=|(umJJRY~k|)0J'u~|Y)lmu)Y; CURRENT_QUALITY=80; DedeUserID=1041142819; DedeUserID__ckMd5=367f5cee99b8f9b1; FEED_LIVE_VERSION=V_WATCHLATER_PIP_WINDOW3; header_theme_version=CLOSE; bsource=search_bing; b_lsid=F1DF686B_18EEA26C4D5; SESSDATA=9d19ff09%2C1728877394%2C04088%2A41CjBLowk7P-sRqrvFQIE1kVsehuuufJKW_Pr_arrM_m4tLST_KPWgxMqhY9hz8kNAXUESVlFSQ0lPcTYxcXFIOEJkcTJkNWVScDRtenVZVjZ2WGticEYwM091WV9FNmtFSV9sMlJKMUJuSDFCZUdDNlV2U2l5Z2pXYUFSQlJtc1BtLTFxTjVnNXZnIIEC; bili_jct=98ccc2dee211106854fe5fc756a5d82d; sid=7vn4ekic; bp_video_offset_1041142819=920964661868757013; home_feed_column=4; browser_resolution=1144-804; bili_ticket=eyJhbGciOiJIUzI1NiIsImtpZCI6InMwMyIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3MTM1ODQ2MTAsImlhdCI6MTcxMzMyNTM1MCwicGx0IjotMX0.wcmWBI5jKSQI4Ygu-BC3V_1s7Gz5kOuV2psCV4aflA4; bili_ticket_expires=1713584550; PVID=2"   # 替换为你的cookie信息
}

def download_subtitle(url):
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            subtitle_data = response.json().get('body')
            if isinstance(subtitle_data, list):
                return ' '.join([item.get('content', '') for item in subtitle_data if isinstance(item, dict) and item.get('content')])
            elif isinstance(subtitle_data, str):
                return subtitle_data
            else:
                print("Unexpected subtitle data format")
        else:
            print(f"Failed to download subtitle: {response.status_code}")
    except requests.RequestException as e:
        print(f"Error downloading subtitle from {url}: {e}")
    return ""

# 初始化字幕列
video_data['字幕'] = ""

for index, row in video_data.iterrows():
    bvid = row['BV号']
    cid = row['CID']
    print(f"Processing BV: {bvid}, CID: {cid}")
    subtitle_url = f'https://api.bilibili.com/x/player/v2?bvid={bvid}&cid={cid}'
    
    try:
        response = requests.get(subtitle_url, headers=headers)
        if response.status_code == 200:
            response_json = response.json()
            print(f"Response JSON for BV: {bvid}, CID: {cid} -> {response_json}")
            subtitles = response_json.get('data', {}).get('subtitle', {}).get('subtitles', [])
            if subtitles:
                subtitle_url = 'https:' + subtitles[0]['subtitle_url']
                subtitle_text = download_subtitle(subtitle_url)
                if subtitle_text:
                    video_data.at[index, '字幕'] = subtitle_text.replace('\n', ' ').replace('"', "'")
                    print(f"Added subtitle for BV: {bvid}")
            else:
                print(f"No subtitles found for BV: {bvid}")
        else:
            print(f"Failed to fetch subtitles for BV: {bvid} with status code {response.status_code}")
    except requests.RequestException as e:
        print(f"Network error while fetching subtitles for {bvid}: {e}")

# 输出文件夹
output_folder = "C:/Users/48842/Desktop/B站爬虫"
os.makedirs(output_folder, exist_ok=True)

# 保存更新后的 DataFrame
output_file_path = os.path.join(output_folder, 'updated_merged_unique_with_subtitles.csv')
video_data.to_csv(output_file_path, index=False, encoding='utf-8')
print("Updated CSV with subtitles has been saved.")
