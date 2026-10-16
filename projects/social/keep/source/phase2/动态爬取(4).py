import json
import os
import csv
import codecs
from urllib.request import urlretrieve

file_name = 'td35.csv'
# 文件的保存路径 
path = file_name


with codecs.open(path, 'a', 'utf-8-sig') as csvfile:
                filednames = ['用户名', '标题', '内容', '评价内容']
                writer = csv.DictWriter(csvfile, fieldnames=filednames)
                writer.writeheader()
                json1="C:\\Users\\48842\\Desktop\\514_.json"
                with open(json1, 'r', encoding='utf-8-sig') as file_in:
                        json_dict=json.load(file_in)
#                        print(json_dict)
                        json_dict=json_dict['data']
                        title=None

                        for n in range(len(json_dict)):
                            if json_dict[n]['info']['entries']!=None:

                                for i in range(len(json_dict[n]['info']['entries'])):
                                        if json_dict[n]['info'].__contains__('entries'):
                                                    username = json_dict[n]['info']['entries'][i]['author']['username']
#                                                    print(username)
                                        else:
                                                        username=None

                                        if json_dict[n]['info'].__contains__('entries'):
                                            if json_dict[n]['info']['entries'][i].__contains__('title'):
                                                title = json_dict[n]['info']['entries'][i]['title']
#                                                print(title)
                                        else:
                                                title=None


                                        if json_dict[n]['info'].__contains__('entries'):
                                            content = json_dict[n]['info']['entries'][i]['content']
#                                            print(content)
                                        else:
                                                    content=None
                                                
                                                
                                                
                                        if json_dict[n]['info'].__contains__('entries'):
                                                for j in range(len(json_dict[n]['info']['entries'][i]['sampleComments'])):
                                                        content2 = json_dict[n]['info']['entries'][i]['sampleComments'][j]['content']
#                                                        print(content2)
                                                        writer.writerow({'评价内容': content2})
                                        else:
                                                   content2 = None
                                                
                                                    
                
                                        writer.writerow({
                                                                 '用户名': username,
                                                                 '标题': title,
                                                                 '内容': content,
                                                                 '评价内容': content2,
                                        })
