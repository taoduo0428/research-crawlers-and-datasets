import os

import ddddocr 
import pandas as pd
import requests
from DrissionPage import ChromiumPage, ChromiumOptions 

co = ChromiumOptions()
co.incognito()  # 无痕模式
co.headless()  # 无头模式
co.no_imgs()  # 无图模式
page = ChromiumPage(co)

proxies = {
    'http': '127.0.0.1:7890',
    'https': '127.0.0.1:7890',
}


def get_info(asin):
    path = 'data/amazon/goods_infos.csv'
    tag_path = 'data/amazon/customers_say_tags.csv'
    if not os.path.exists(path):
        pd.DataFrame(columns=['ASIN', '链接', '标题', '评分', '评分数', '标签', '价格', 'amazon global shipping', '商品介绍', 'Customers say']).to_csv(path, index=False)
    if not os.path.exists(tag_path):
        pd.DataFrame(columns=['ASIN', '标签', '标签属性', 'customers_mention', 'positive', 'negative', '标签内容']).to_csv(tag_path, index=False)
        # 读取CSV文件到DataFrame
    # 读取CSV文件到DataFrame
    df = pd.read_csv(path)
    # 检查数据是否已存在
    if (df['ASIN'] == asin).any():
        print("数据已存在，无需添加。")
        return
    print(f'正在采集 {asin} 商品数据...')
    # 请求页面
    url = f'https://www.amazon.com/dp/{asin}?language=en_US'
    page.get(url)
    # 触发验证码
    cha_img_div = page.ele('.a-row a-text-center', timeout=1)
    if cha_img_div:
        print('触发验证码')
        cha_img_src = cha_img_div.ele('tag:img').attr('src')
        print(cha_img_src)
        req = requests.get(cha_img_src, proxies=proxies)
        orc = ddddocr.DdddOcr(show_ad=False, beta=True)
        text = orc.classification(req.content)
        print(text)
        page.ele('tag:input@class=a-span12').input(text)
        page.ele('.a-button-text').click()
        page.wait.load_start()
        if not page.ele('.a-row a-text-center', timeout=0.5):
            print('验证码通过')
        else:
            print('验证码失败，重试')
            get_info(asin)
    # 提取数据
    item = {}
    item['ASIN'] = asin
    item['链接'] = url
    item['标题'] = page.ele('#productTitle').text if page.ele('#productTitle', timeout=0.5) else ''
    item['评分'] = page.ele('.a-size-medium a-color-base').text.split(' ')[0] if page.ele('.a-size-medium a-color-base', timeout=0.5) else ''
    item['评分数'] = int(page.ele('#acrCustomerReviewText').text.split(' ')[0].replace(',', '')) if page.ele('#acrCustomerReviewText', timeout=0.5) else ''
    tag = page.ele('.a-icon a-icon-addon p13n-best-seller-badge', timeout=0.1)
    if tag:
        item['标签'] = tag.text
    else:
        item['标签'] = ''
    item['价格'] = page.ele('.a-offscreen').text if page.ele('.a-offscreen', timeout=0.5) else ''
    lineitem = page.ele('.a-lineitem', timeout=0.1)
    if lineitem:
        item['amazon global shipping'] = lineitem.ele('x:./tbody/tr[2]/td[3]/span').text
    else:
        item['amazon global shipping'] = ''
    item['商品介绍'] = '\n'.join(page.ele('.a-unordered-list a-vertical a-spacing-mini').texts()).replace(',', '，') if page.ele('.a-unordered-list a-vertical a-spacing-mini', timeout=0.5) else ''
    # 判断是否存在 Customers say
    customers_say = page.ele('#cr-product-insights-cards', timeout=0.1)
    if customers_say:
        item['Customers say'] = customers_say.ele('.a-spacing-small').texts()[0].replace(',', '，')
        # 提取Customers say标签数据
        tag_list = customers_say.ele('#aspect-button-group-0').eles('tag:button')
        tag_info_list = customers_say.ele('._cr-product-insights_style_insight-bottom-sheet__xeFzj').eles('tag:div@class=_cr-product-insights_style_insight-bottom-sheet-content__1KIl7')
        for i in range(len(tag_list)):
            tag_item = {}
            tag_item['ASIN'] = asin
            tag_item['标签'] = tag_list[i].texts()[0]
            svg = tag_list[i].ele('tag:svg', timeout=0)
            if svg:
                color = svg.ele('tag:path').attr('fill')
                if color == '#067D62':
                    tag_item['标签属性'] = '1'
                elif color == '#DE7921':
                    tag_item['标签属性'] = '-1'
                else:
                    tag_item['标签属性'] = color
            else:
                tag_item['标签属性'] = '0'
            tag_item['customers_mention'] = tag_info_list[i].ele('text:customers mention').text.split(' ')[0]
            tag_item['positive'] = tag_info_list[i].ele('text:positive').text.split(' ')[0]
            tag_item['negative'] = tag_info_list[i].ele('text:negative').text.split(' ')[0]
            tag_item['标签内容'] = '\n'.join(tag_info_list[i].ele('tag:p').texts())
            print('标签数据：', tag_item)
            # 保存Customers say标签数据
            df = pd.DataFrame([tag_item])
            df.to_csv(tag_path, index=False, mode='a', header=False)
    else:
        item['Customers say'] = ''
    print('商品数据：', item)
    # 保存商品数据
    df = pd.DataFrame([item])
    df.to_csv(path, index=False, mode='a', header=False)
    print(f'{asin} 采集完毕')

def read_existing_asins(file_path):
    """从CSV文件中读取已处理的ASIN列表"""
    if os.path.exists(file_path):
        df = pd.read_csv(file_path)
        return set(df['ASIN'])
    return set()

def main():
    # 读取asin文件，获取所有asin
    with open('data/ASIN.txt', 'r', encoding='utf-8') as f:
        asin_list = f.read().split('\n')
    # 遍历asin进行爬取
    for asin in asin_list:
        get_info(asin)
    # get_info('B0CWQ5WYMT')
    print('爬取完成')
    page.quit()


if __name__ == '__main__':
    main()
