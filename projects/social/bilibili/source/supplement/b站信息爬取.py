import re
import os
import csv
import time
import math
import json
import requests
import numpy as np
import pandas as pd
from datetime import datetime
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
 
 
def merge_csv(input_filename, output_filename):
 
    # 读取文件
    csv_data_read = pd.read_csv(input_filename)
    # 获取文件总行数
    number_of_row = (len(csv_data_read))
    # 循环该csv文件中的所有行，并写入信息
    for i in range(0, number_of_row):
        row_info = csv_data_read.values[i]
        # 输出查看内容
        # print(row_info)
        # 具体内容
        row_content = row_info[0]
        # 写入
        write_to_csv_bvid(output_filename, row_content)
        # 退出循环
    # 打印进度
    print(f'成功向{output_filename}中写入了{input_filename}的全部信息')
 
 
def write_to_csv_bvid(input_filename, bvid):

    # OS 判断路径是否存在
    file_exists = os.path.isfile(input_filename)
    # 设置最大尝试次数
    max_retries = 50
    retries = 0
 
    while retries < max_retries:
        try:
            with open(input_filename, mode='a', encoding='utf-8', newline='') as csvfile:
                fieldnames = ['BV号']
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
 
                if not file_exists:
                    writer.writeheader()
 
                writer.writerow({
                    'BV号': bvid
                })
                # print('写入文件成功')
            break  # 如果成功写入，跳出循环
        except PermissionError as e:
            retries += 1
            print(f"将爬取到的数据写入csv时，遇到权限错误Permission denied，文件可能被占用或无写入权限: {e}")
            print(f"等待10s后重试，将会重试50次... (尝试 {retries}/{max_retries})")
            time.sleep(5)  # 等待10秒后重试
    else:
        print("将爬取到的数据写入csv时遇到权限错误，且已达到最大重试次数50次，退出程序")
 
 
def spider_bvid(keyword):

    # 保存的文件名
    input_filename = f'{keyword}BV号.csv'
 
    # 启动爬虫
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--disable-gpu')
    browser = webdriver.Chrome(options=options)  # 设置无界面爬虫
    browser.set_window_size(1400, 900)  # 设置全屏，注意把窗口设置太小的话可能导致有些button无法点击
    browser.get('https://bilibili.com')
    # 刷新一下，防止搜索button被登录弹框遮住
    browser.refresh()
    print("============成功进入B站首页！！！===========")
    input = browser.find_element(By.CLASS_NAME, 'nav-search-input')
    button = browser.find_element(By.CLASS_NAME, 'nav-search-btn')
 
    # 输入关键词并点击搜索
    input.send_keys(keyword)
    button.click()
    print(f'==========成功搜索{keyword}相关内容==========')
 
    # 设置窗口
    all_h = browser.window_handles
    browser.switch_to.window(all_h[1])

 
    # B站最多显示42页
    total_page = 1
    # 同样由于B站网页代码的更改，通过找到并点击下一页的方式个人暂不能实现（对，不会分析那个破网页！！！）
    # 因此这里利用总页数进行循环访问来实现自动翻页的效果
 
    for i in range(0, total_page):
        # url 需要根据不同关键词进行调整内容！！！
        url = (f"https://search.bilibili.com/all?keyword={keyword}"
               f"&from_source=webtop_search&spm_id_from=333.1007&search_source=5&page={i}")
 
        print(f"===========正在尝试获取第{i + 1}页网页内容===========")
        print(f"===========本次的url为：{url}===========")
        browser.get(url)
        # 这里请求访问网页的时间也比较久(可能因为我是macos)，所以是否需要等待因设备而异
        # 取消刷新并长时间休眠爬虫以避免爬取太快导致爬虫抓取到js动态加载源码
        # browser.refresh()
        print('正在等待页面加载：3')
        time.sleep(5)
        print('正在等待页面加载：2')
        time.sleep(3)
        print('正在等待页面加载：1')
        time.sleep(1)
 
        # 直接分析网页
        html = browser.page_source
        # print("网页源码" + html) 用于判断是否获取成功
        soup = BeautifulSoup(html, 'lxml')
        infos = soup.find_all(class_='bili-video-card')
        bv_id_list = []
        for info in infos:
            # 只定位视频链接
            href = info.find('a').get('href')
            # 拆分
            split_url_data = href.split('/')
            # 利用循环删除拆分出现的空白
            for element in split_url_data:
                if element == '':
                    split_url_data.remove(element)
            # 打印检验内容
            # print(split_url_data)
            # 获取bvid
            bvid = split_url_data[2]
 
            # 利用if语句直接去重
            if bvid not in bv_id_list:
                bv_id_list.append(bvid)
        for bvid_index in range(0, len(bv_id_list)):
            # 写入 input_filename
            write_to_csv_bvid(input_filename, bv_id_list[bvid_index])
        # 输出提示进度
        print('写入文件成功')
        print("===========成功获取第" + str(i + 1) + "次===========")
        time.sleep(10)
        i += 1
 
    # 退出爬虫
    browser.quit()
 
    # 打印信息显示是否成功
    print(f'==========爬取完成。退出爬虫==========')
 
 
def write_to_csv(filename, bvid, aid, cid, mid, name, follower, archive, title, tname, pub_date, pub_time, desc,
                 view, like, coin, favorite, share, reply, danmaku):
    """
    向csv文件中写入B站视频相关的基本信息，未按路径找到文件，则新建文件
    :param filename: 写入数据的文件名
    :param bvid: BV号
    :param aid: AV号
    :param cid: 用于获取弹幕文本的
    :param mid: UP主的ID
    :param name: UP主名称
    :param follower: UP主粉丝数
    :param archive: UP主作品总数
    :param title: 标题
    :param tname: tag名称
    :param pub_date: 发布日期
    :param pub_time: 发布时间
    :param desc: 视频简介
    :param view: 播放量
    :param like: 点赞数
    :param coin: 投币数
    :param favorite: 收藏数
    :param share: 分享数
    :param reply: 评论数
    :param danmaku: 弹幕数
    :param communication_index: 传播效果公式的值
    :return:
    """
    file_exists = os.path.isfile(filename)
    max_retries = 50
    retries = 0
 
    while retries < max_retries:
        try:
            with open(filename, mode='a', encoding='utf-8', newline='') as csvfile:
                fieldnames = ['BV号', 'AV号', 'CID', 'UP主ID', 'UP主名称', 'UP主粉丝数', '作品总数', '视频标题',
                              '视频分类标签',
                              '发布日期', '发布时间', '视频简介', '播放量', '点赞数', '投币数', '收藏数', '分享数',
                              '评论数',
                              '弹幕数']
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
 
                if not file_exists:
                    writer.writeheader()
 
                writer.writerow({
                    'BV号': bvid, 'AV号': aid, 'CID': cid, 'UP主ID': mid, 'UP主名称': name, 'UP主粉丝数': follower,
                    '作品总数': archive, '视频标题': title, '视频分类标签': tname, '发布日期': pub_date,
                    '发布时间': pub_time,
                    '视频简介': desc, '播放量': view, '点赞数': like, '投币数': coin, '收藏数': favorite,
                    '分享数': share,
                    '评论数': reply, '弹幕数': danmaku 
                })
            break  # 如果成功写入，跳出循环
        except PermissionError as e:
            retries += 1
            print(f"将爬取到的数据写入csv时，遇到权限错误Permission denied，文件可能被占用或无写入权限: {e}")
            print(f"等待3s后重试，将会重试50次... (尝试 {retries}/{max_retries})")
    else:
        print("将爬取到的数据写入csv时遇到权限错误，且已达到最大重试次数50次，退出程序")
 
 
def get_user_info(uid):
    """
    通过uid(即mid)获取UP主的粉丝总数和作品总数
    :param uid: mid
    :return:user_info_dict
    """
    # 定义空字典用于存放数据
    # 粉丝数 follower
    # 作品总数 archive
    user_info_dict = {}
    # 首先写入请求头
    # 设置用户代理 User_Agent及Cookies
    headers = {
        'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Cookie': 'buvid3=07E695D8-DC99-1CE7-0BD5-1AD40BC9EF5079673infoc; b_nut=1677637579; i-wanna-go-back=-1; _uuid=513BB983-88B7-5F810-69AD-75B3101A23810C93257infoc; DedeUserID=520931432; DedeUserID__ckMd5=85ebb83199377c61; rpdid=0z9ZwfQeoZ|14XAXesu|4ri|3w1Pxee5; b_ut=5; buvid4=AD5B8430-8417-F6DB-C9F2-623249C92FF393031-023030112-7XDfT9HnZ75f7eH4%2BIxIp4BKQv9vkuaEPPrtVRI1MkYCidM8Mtxh2w%3D%3D; buvid_fp_plain=undefined; LIVE_BUVID=AUTO1516781971255759; hit-dyn-v2=1; nostalgia_conf=-1; CURRENT_PID=c5731e30-c89d-11ed-8e21-d7631bcaf686; hit-new-style-dyn=1; is-2022-channel=1; header_theme_version=CLOSE; CURRENT_BLACKGAP=0; FEED_LIVE_VERSION=V8; CURRENT_FNVAL=4048; enable_web_push=DISABLE; CURRENT_QUALITY=120; buvid_fp=ecb7dd28792e93e5277f4442aae2d4c6; fingerprint=aa0121758f5e8f5253ebe2b68898acfe; PVID=1; bp_video_offset_520931432=894245691817197600; SESSDATA=6f7eb495%2C1722675706%2C66d4c%2A21CjCf36FNLhsY83qz1D3jD97FqWRC3cWDfpFseASOny7-BVuF5vEJscr7LJxvUiMcHMISVmpLM1I0WDN1M1hvMFNOSmlCcEwya0JWWlBqQjU2UUQ5VjVjQUxHTVBYaF9hWEJtc1h0UTNubmx6c3hsQWJCaGJZbElqZlI5eTU1cXZqUnRDWkJxNTBRIIEC; bili_jct=acb09ff334f932433ba231ab32a94f5a; sid=5qx6tg0r; b_lsid=1EB6941D_18D7C2EBC10; home_feed_column=4; browser_resolution=780-898'}
 
    # 将传入的的uid组成up主主页的api_url
    # A Example: https://api.bilibili.com/x/web-interface/card?mid=1177893348
    api_url = f'https://api.bilibili.com/x/web-interface/card?mid={uid}'
    # https://api.bilibili.com/x/web-interface/view?BV1n24y1D75V
    # 打印次数，数据量大，便于查看进程
    print(f"正在进行爬取uid为：{uid}的UP主的粉丝数量与作品总数")
 
    # 打印本次要获取的uid，用于错误时确认
    print(f"==========本次获取数据的up主的uid为：{uid}==========")
    print(f"url为{api_url}")
 
    # 利用requests进行访问，并返回需要的封装信息
    up_info = requests.get(url=api_url, headers=headers)
 
    # 不知道会不会被封ip，保险起见
    time.sleep(5)
 
    # 将数据转化为json格式
    up_info_json = json.loads(up_info.text)
 
    # 利用json定位相关数据
    fans_number = up_info_json['data']['card']['fans']
    user_info_dict['follower'] = fans_number
    archive_count = up_info_json['data']['archive_count']
    user_info_dict['archive'] = archive_count
 
    print(f'=========={bv_id} 的作者基本信息已成功获取==========\n')
 
    # 等待
    print('正在等待，以防访问过于频繁\n')
    time.sleep(3)
    return user_info_dict
 
 
def get_video_info(bv_id):
    headers = {
        'User-Agent': "",
        'Cookie': ""}
 
    api_url = f'https://api.bilibili.com/x/web-interface/view?bvid={bv_id}'
    # 打印本次要获取的bvid，用于错误时确认
    print(f"正在进行爬取uid为：{bv_id}的UP主的粉丝数量与作品总数")
    print(f"==========本次获取数据的视频BV号为：{bv_id}==========")
    print(f"url为：{api_url}")
    # https://api.bilibili.com/x/web-interface/view?BV1n24y1D75V
    video_info = requests.get(url=api_url, headers=headers)
    video_info_json = json.loads(video_info.text)
    # 创建存放的字典
    info_dict = {}
    # 信息解读
    # https://zhuanlan.zhihu.com/p/618885790
    # 视频bvid，即bv号
    bvid = video_info_json['data']['bvid']
    info_dict['bvid'] = bvid
    # 视频aid，即av号
    aid = video_info_json['data']['aid']
    info_dict['aid'] = aid
    # 视频cid，用于获取弹幕信息
    cid = video_info_json['data']['cid']
    info_dict['cid'] = cid
    # 作者id
    mid = video_info_json['data']['owner']['mid']
    info_dict['mid'] = mid
    # up主昵称
    name = video_info_json['data']['owner']['name']
    info_dict['name'] = name
    # 视频标题
    title = video_info_json['data']['title']
    info_dict['title'] = title
    # 视频标签
    tname = video_info_json['data']['tname']
    info_dict['tname'] = tname
    # 视频发布时间戳
    pubdate = video_info_json['data']['pubdate']
    # 转化时间戳
    pub_datatime = datetime.fromtimestamp(pubdate)
    # 整体格式
    pub_datatime_strf = pub_datatime.strftime('%Y-%m-%d %H:%M:%S')
    # 日期
    date = re.search(r"(\d{4}-\d{1,2}-\d{1,2})", pub_datatime_strf)
    info_dict['pub_date'] = date.group()
    # 时间
    pub_time = re.search(r"(\d{1,2}:\d{1,2}:\d{1,2})", pub_datatime_strf)
    info_dict['pub_time'] = pub_time.group()
    # 视频创建时间戳
    # ctime = info['ctime']
    # 视频简介
    desc = video_info_json['data']['desc']
    info_dict['desc'] = desc
    # 视频播放量
    view = video_info_json['data']['stat']['view']
    info_dict['view'] = view
    # 点赞数
    like = video_info_json['data']['stat']['like']
    info_dict['like'] = like
    # 投币数
    coin = video_info_json['data']['stat']['coin']
    info_dict['coin'] = coin
    # 收藏数
    favorite = video_info_json['data']['stat']['favorite']
    info_dict['favorite'] = favorite
    # 分享数
    share = video_info_json['data']['stat']['share']
    info_dict['share'] = share
    # 评论数
    repiy = video_info_json['data']['stat']['reply']
    info_dict['reply'] = repiy
    # 视频弹幕数量
    danmaku = video_info_json['data']['stat']['danmaku']
    info_dict['danmaku'] = danmaku
 
    print(f'=========={bv_id} 的视频基本信息已成功获取==========')
 
    # 发布作品时的动态
    # dynamic = info['dynamic']
    print('正在等待，以防访问过于频繁\n')
    time.sleep(3)
 
    return info_dict
 
 
if __name__ == '__main__':
 
    # 针对不同内容修改搜索关键词！！！！
    keywords = ["AAA"]
    for keyword in keywords:
        # 自动爬取多个主题时须注意上面的最大页数定位问题
        # 爬取后生成去重了的len(keywords)个f'{keyword}BV号.csv'文件
        spider_bvid(keyword)
    for keyword in keywords:
        # 拼接成文件名
        csv_to_merge = f'{keyword}BV号.csv'
        # 合并后生成未去重的文件
        merge_csv(input_filename=csv_to_merge, output_filename='BV号合并.csv')
    
 
    # 遍历读取bv_id
    filename = 'BV号合并.csv'
    # 打开文件并去重
    open_csv = pd.read_csv(filename)
    open_csv.drop_duplicates(subset='BV号')
    bv_id_list = np.array(open_csv['BV号'])
 
    """
    # 第一次调用，若读取csv进行爬取时，意外中断
    # 则更改为读取txt文本，将已爬取第bvid删除，以达到断点续爬的目的
    for bvid in bv_id_list:
        with open("bv_id_list.txt", 'a') as f:
            f.write(bvid+'\n')
    with open("bv_id_list.txt", 'r') as f:
        bv_id_list = f.readlines()
    """
 
    
    # 循环写入内容
    for i in range(0, len(bv_id_list)):
        bv_id = bv_id_list[i]
        print(f'正在进行第{i+1}次爬取\n')
        # 获取视频所有的基本信息
        video_info = get_video_info(bv_id)
        bvid = video_info['bvid']
        aid = video_info['aid']
        cid = video_info['cid']
        mid = video_info['mid']
        name = video_info['name']
        title = video_info['title']
        tname = video_info['tname']
        pub_date = video_info['pub_date']
        pub_time = video_info['pub_time']
        desc = video_info['desc']
        view = video_info['view']
        like = video_info['like']
        coin = video_info['coin']
        favorite = video_info['favorite']
        share = video_info['share']
        reply = video_info['reply']
        danmaku = video_info['danmaku']
 
        # 获取作者的相关信息
        user_info = get_user_info(uid=mid)
        follower = user_info['follower']
        archive = user_info['archive']
        write_to_csv(filename='视频基本信息.csv', bvid=bvid, aid=aid, cid=cid, mid=mid, name=name, follower=follower,
                     archive=archive, title=title, tname=tname, pub_date=pub_date, pub_time=pub_time, desc=desc,
                     view=view, like=like, coin=coin, favorite=favorite, share=share, reply=reply, danmaku=danmaku,
                     )
        print(f'==========第{i+1}个BV号：{bv_id}的相关数据已写入csv文件中==========')
        print('==================================================\n')
