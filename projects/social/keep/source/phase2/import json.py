import json
import os
import csv
import codecs

file_name = '85201.csv'
# 文件的保存路径 
path = file_name

# 桌面路径
desktop_path = "C:\\Users\\48842\\Desktop\\chenggong1"

with codecs.open(path, 'a', 'utf-8-sig') as csvfile:
    filednames = ['用户名', '标题', '内容', '评价内容']
    writer = csv.DictWriter(csvfile, fieldnames=filednames)
    writer.writeheader()

    # 遍历桌面上的所有文件
    for file in os.listdir(desktop_path):
        # 如果文件是json文件
        if file.endswith(".json"):
            json_path = os.path.join(desktop_path, file)
            with open(json_path, 'r', encoding='utf-8-sig') as file_in:
                json_dict = json.load(file_in)
                json_dict = json_dict['data']
                title = None

                for n in range(len(json_dict)):
                    if json_dict[n]['info']['entries'] is not None:
                        for i in range(len(json_dict[n]['info']['entries'])):
                            username = json_dict[n]['info']['entries'][i]['author']['username'] if json_dict[n]['info'].__contains__('entries') else None
                            title = json_dict[n]['info']['entries'][i]['title'] if json_dict[n]['info'].__contains__('entries') and json_dict[n]['info']['entries'][i].__contains__('title') else None
                            content = json_dict[n]['info']['entries'][i]['content'] if json_dict[n]['info'].__contains__('entries') else None
                            content2 = None
                            if json_dict[n]['info'].__contains__('entries'):
                                sampleComments = json_dict[n]['info']['entries'][i]['sampleComments']
                                if sampleComments is not None:
                                    for j in range(len(sampleComments)):
                                        content2 = sampleComments[j]['content']
                                        writer.writerow({'评价内容': content2})
                            writer.writerow({
                                '用户名': username,
                                '标题': title,
                                '内容': content,
                                '评价内容': content2,
                            })
