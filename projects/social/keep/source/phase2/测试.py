import json
import os
import csv
import codecs

file_name = '68.csv'
path = file_name

with codecs.open(path, 'a', 'utf-8-sig') as csvfile:
    fieldnames = ['用户名', '标题', '内容', '评价内容']
    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
    writer.writeheader()

    json1 = "C:\\Users\\48842\Desktop\\新建文件夹" # 修改这行为你的桌面路径
    
    try:
        with open(json1, 'r', encoding='utf-8-sig') as file_in:
            json_dict = json.load(file_in)
            
            for n in range(len(json_dict)):
                if 'info' in json_dict[n] and 'entries' in json_dict[n]['info']:
                    for i in range(len(json_dict[n]['info']['entries'])):
                        username = json_dict[n]['info']['entries'][i].get('author', {}).get('username', None)
                        title = json_dict[n]['info']['entries'][i].get('title', None)
                        content = json_dict[n]['info']['entries'][i].get('content', None)
                        
                        writer.writerow({'用户名': username, '标题': title, '内容': content, '评价内容': your_review_content})
    except FileNotFoundError:
        print(f"File {json1} not found.")
    except json.JSONDecodeError:
        print(f"Error decoding JSON in file {json1}.")
