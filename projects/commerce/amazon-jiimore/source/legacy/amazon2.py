import os
import pandas as pd
from DrissionPage import ChromiumPage, ChromiumOptions

def setup_browser():
    """配置并返回一个浏览器实例。"""
    co = ChromiumOptions()
    co.incognito()  # 无痕模式
    co.headless()  # 无头模式
    co.no_imgs()  # 无图模式
    return ChromiumPage(co)

def update_global_shipping(asin, page, path):
    """更新特定ASIN的Amazon全球配送信息。"""
    print(f'正在更新 {asin} 的全球配送信息...')
    url = f'https://www.amazon.com/dp/{asin}?language=en_US'
    page.get(url)
    
    # 处理验证码
    cha_img_div = page.ele('.a-row a-text-center', timeout=1)
    if cha_img_div:
        print('触发验证码')
        return False  # 验证码触发，跳过此ASIN

    # 尝试获取全球配送信息
    shipping_info = ''
    lineitem = page.ele('.a-lineitem', timeout=0.1)
    if lineitem:
        shipping_info_element = lineitem.ele('x:./tbody/tr[2]/td[3]/span')
        if shipping_info_element:
            shipping_info = shipping_info_element.text

    # 更新CSV文件
    if os.path.exists(path):
        df = pd.read_csv(path)
        if asin in df['ASIN'].values:
            df.loc[df['ASIN'] == asin, 'amazon global shipping'] = shipping_info
        else:
            new_data = pd.DataFrame([[asin, '', '', '', '', '', '', shipping_info, '', '']],
                                    columns=['ASIN', '链接', '标题', '评分', '评分数', '标签', '价格', 'amazon global shipping', '商品介绍', 'Customers say'])
            df = pd.concat([df, new_data], ignore_index=True)
        df.to_csv(path, index=False)
        print(f'{asin} 的全球配送信息已更新。')
        return True
    else:
        print('目标文件不存在，请检查路径。')
        return False

def main():
    path = 'data/amazon/goods_infos.csv'
    page = setup_browser()

    # 确保目标文件存在
    if not os.path.exists(path):
        print('CSV文件不存在，退出程序。')
        return
    
    # 读取CSV文件中需要更新的ASIN
    df = pd.read_csv(path)
    missing_shipping_asins = df[df['amazon global shipping'].isna()]['ASIN'].tolist()

    # 从ASIN文件中读取ASIN列表
    with open('data/ASIN.txt', 'r', encoding='utf-8') as f:
        all_asins = f.read().split('\n')
    
    # 筛选出需要更新的ASIN
    asins_to_process = [asin for asin in all_asins if asin in missing_shipping_asins]
    
    # 更新缺失的全球配送信息
    for asin in asins_to_process:
        if asin.strip():  # 确保ASIN不为空
            success = update_global_shipping(asin.strip(), page, path)
            if not success:
                print(f'处理 {asin} 时发生错误，可能需要人工介入。')

    print('所有需要更新的ASIN的全球配送信息处理完毕。')
    page.quit()

if __name__ == '__main__':
    main()
