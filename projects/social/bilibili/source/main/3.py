import pandas as pd
import requests
import os

video_data_path = "C:/Users/48842/Desktop/视频基本信息.csv"
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


headers = {
    'authority': 'api.bilibili.com',
    'accept': 'application/json, text/plain, */*',
    'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
    'origin': 'https://www.bilibili.com',
    'referer': 'https://www.bilibili.com/',
    'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'cookie': "CURRENT_FNVAL=4048; enable_web_push=DISABLE; buvid3=0EBBF7BB-FE14-88F8-E78C-4C732780DCCD71579infoc; b_nut=1706859171; i-wanna-go-back=-1; b_ut=7; _uuid=77556E410-5B1D-A147-C45C-C67631D575BC72835infoc; buvid4=93E3B393-C104-C800-3418-408C939DF4CA73042-024020207-IAAnKFSLv27gVhP1IipP0w%3D%3D; rpdid=|(umJJRY~k|)0J'u~|Y)lmu)Y; CURRENT_QUALITY=80; DedeUserID=1041142819; DedeUserID__ckMd5=367f5cee99b8f9b1; FEED_LIVE_VERSION=V_WATCHLATER_PIP_WINDOW3; header_theme_version=CLOSE; fingerprint=9421df2d3b267e6c8735ce525ea1f004; buvid_fp_plain=undefined; buvid_fp=55dedf4b3b36eba7b6cfce63266904db; LIVE_BUVID=AUTO8217153416417219; PVID=3; b_lsid=D1791A35_1901242930B; bsource=search_bing; SESSDATA=8249bc4e%2C1733845273%2Ca02ef%2A62CjC5UePzxwLIZNzpJu-yvEYZwqEjjvyuB1BmJvCHeCfxIC5IXMvUNvNzI2slr1_XWD0SVnVZeXJDdU1SWkV1NjJsa3k1YXNWNzZyc0hJczRLWHRFblRJSmh4ZWlaZG1ib2t0SVdLZ0pQbEJuNE55X1I2OU56YzQ0R011NDdlbWE0UzN4eVd1U1BnIIEC; bili_jct=9d0ca6a769e126e4c6113ed12bccbfea; sid=4scbri26; bp_t_offset_1041142819=942480962207350822; home_feed_column=4; browser_resolution=1022-804"  # 替换为你的cookie信息
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

video_data['字幕'] = ""

for index, row in video_data.iterrows():
    bvid = row['BV号']
    cid = row['CID']
    print(f"Processing BV: {bvid}, CID: {cid}")
    subtitle_url = f'https://api.bilibili.com/x/player/v2?bvid={bvid}&cid={cid}'
    
    try:
        response = requests.get(subtitle_url, headers=headers)
        if response.status_code == 200:
            subtitles = response.json().get('data', {}).get('subtitle', {}).get('subtitles', [])
            if subtitles:
                subtitle_url = 'https:' + subtitles[0]['subtitle_url']
                subtitle_text = download_subtitle(subtitle_url)
                if subtitle_text:
                    video_data.at[index, '字幕'] = subtitle_text.replace('\n', ' ').replace('"', "'")
                    print(f"Added subtitle for BV: {bvid}")
            else:
                print(f"No subtitles found for BV: {bvid}")
        else:
            print(f"Failed to fetch subtitles for BV: {bvid}")
    except requests.RequestException as e:
        print(f"Network error while fetching subtitles for {bvid}: {e}")
    except Exception as e:
        print(f"Unexpected error while processing BV: {bvid}, CID: {cid}: {e}")


output_file_path = "C:/Users/48842/Desktop/updated_merged_unique_with_subtitles.csv"
try:
    video_data.to_csv(output_file_path, index=False, encoding='utf-8')
    print("Updated CSV with subtitles has been saved to the desktop.")
except Exception as e:
    print(f"Error saving the updated CSV file: {e}")
