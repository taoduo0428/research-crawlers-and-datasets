import os
import ddddocr
import pandas as pd
import requests
from DrissionPage import ChromiumPage, ChromiumOptions
from concurrent.futures import ThreadPoolExecutor

# 配置浏览器选项
def setup_browser():
    co = ChromiumOptions()
    co.incognito()  # 无痕模式
    co.headless()  # 无头模式
    co.no_imgs()  # 无图模式
    return ChromiumPage(co)

# 代理配置
proxies = {
    'http': '127.0.0.1:7890',
    'https': '127.0.0.1:7890',
}

# 初始化数据文件
def init_files():
    data_path = 'data/amazon'
    os.makedirs(data_path, exist_ok=True)
    goods_info_path = os.path.join(data_path, 'goods_infos.csv')
    tags_info_path = os.path.join(data_path, 'customers_say_tags.csv')
    if not os.path.exists(goods_info_path):
        pd.DataFrame(columns=['ASIN', '链接', '标题', '评分', '评分数', '标签', '价格', 'amazon global shipping', '商品介绍', 'Customers say']).to_csv(goods_info_path, index=False)
    if not os.path.exists(tags_info_path):
        pd.DataFrame(columns=['ASIN', '标签', '标签属性', 'customers_mention', 'positive', 'negative', '标签内容']).to_csv(tags_info_path, index=False)
    return goods_info_path, tags_info_path

# 爬取信息
def get_info(asin, page, goods_info_path, tags_info_path):
    print(f'正在采集 {asin} 商品数据...')
    url = f'https://www.amazon.com/dp/{asin}?language=en_US'
    try:
        page.get(url)
        # 处理验证码
        handle_captcha(page)
        # 提取数据
        item, tags = extract_data(page, asin, url)
        # 保存数据
        save_data(item, tags, goods_info_path, tags_info_path)
        print(f'{asin} 采集完毕')
    except Exception as e:
        print(f"Error processing {asin}: {e}")

# 处理验证码
def handle_captcha(page):
    cha_img_div = page.ele('.a-row a-text-center', timeout=1)
    if cha_img_div:
        print('触发验证码')
        cha_img_src = cha_img_div.ele('tag:img').attr('src')
        req = requests.get(cha_img_src, proxies=proxies)
        orc = ddddocr.DdddOcr(show_ad=False, beta=True)
        text = orc.classification(req.content)
        page.ele('tag:input@class=a-span12').input(text)
        page.ele('.a-button-text').click()
        page.wait.load_start()
        if page.ele('.a-row a-text-center', timeout=0.5):
            raise Exception('验证码处理失败')

# 提取数据
def extract_data(page, asin, url):
    item = {
        'ASIN': asin,
        '链接': url,
        '标题': page.ele('#productTitle', timeout=5).text,
        '评分': page.ele('.a-size-medium a-color-base', timeout=5).text.split(' ')[0],
        '评分数': page.ele('#acrCustomerReviewText', timeout=5).text.replace(',', ''),
        '标签': page.ele('.a-icon a-icon-addon p13n-best-seller-badge', timeout=1).text if page.ele('.a-icon a-icon-addon p13n-best-seller-badge', timeout=1) else '',
        '价格': page.ele('.a-offscreen', timeout=5).text,
        'amazon global shipping': page.ele('.a-lineitem', timeout=1).ele('x:./tbody/tr[2]/td[3]/span').text if page.ele('.a-lineitem', timeout=1) else '',
        '商品介绍': '\n'.join(page.ele('.a-unordered-list a-vertical a-spacing-mini', timeout=5).texts()).replace(',', '，')
    }
    tags = extract_tags(page, asin)
    return item, tags

# 提取Customers say标签数据
def extract_tags(page, asin):
    tags = []
    customers_say = page.ele('#cr-product-insights-cards', timeout=0.1)
    if customers_say:
        tag_list = customers_say.ele('#aspect-button-group-0').eles('tag:button')
        tag_info_list = customers_say.ele('._cr-product-insights_style_insight-bottom-sheet__xeFzj').eles('tag:div@class=_cr-product-insights_style_insight-bottom-sheet-content__1KIl7')
        for i in range(len(tag_list)):
            tag_item = {
                'ASIN': asin,
                '标签': tag_list[i].texts()[0],
                '标签属性': extract_color(tag_list[i]),
                'customers_mention': tag_info_list[i].ele('text:customers mention').text.split(' ')[0],
                'positive': tag_info_list[i].ele('text:positive').text.split(' ')[0],
                'negative': tag_info_list[i].ele('text:negative').text.split(' ')[0],
                '标签内容': '\n'.join(tag_info_list[i].ele('tag:p').texts())
            }
            tags.append(tag_item)
    return tags

# 提取标签颜色属性
def extract_color(tag_element):
    svg = tag_element.ele('tag:svg', timeout=0)
    if svg:
        color = svg.ele('tag:path').attr('fill')
        return '1' if color == '#067D62' else '-1' if color == '#DE7921' else color
    return '0'

# 保存数据
def save_data(item, tags, goods_info_path, tags_info_path):
    pd.DataFrame([item]).to_csv(goods_info_path, mode='a', header=False, index=False)
    if tags:
        pd.DataFrame(tags).to_csv(tags_info_path, mode='a', header=False, index=False)

def main():
    goods_info_path, tags_info_path = init_files()
    asin_list = ['B0CWQ5WYMT']  # 示例ASIN列表
    page = setup_browser()
    for asin in asin_list:
        get_info(asin, page, goods_info_path, tags_info_path)
    page.quit()

if __name__ == '__main__':
    main()
