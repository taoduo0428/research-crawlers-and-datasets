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
    'cookie': os.environ.get('BILIBILI_COOKIE', '')  # 替换为你的cookie信息
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
