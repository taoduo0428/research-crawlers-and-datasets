import os
import pandas as pd
from DrissionPage import ChromiumPage, ChromiumOptions

def get_amazon_global_shipping(page):
    """Get Amazon global shipping information."""
    try:
        lineitem = page.ele('.a-lineitem', timeout=0.1)
        if lineitem:
            shipping_info = lineitem.ele('x:./tbody/tr[2]/td[3]/span')
            if shipping_info:
                return shipping_info.text
    except Exception as e:
        print(f"Could not retrieve 'amazon global shipping' information: {str(e)}")
    return ''

def update_global_shipping(asin, page, path):
    """Fetch and update the Amazon global shipping information for a given ASIN."""
    print(f'Fetching data for {asin}...')
    url = f'https://www.amazon.com/dp/{asin}?language=en_US'
    page.get(url)
    
    # Check for captcha and implement a simple recheck logic if needed
    if page.ele('.a-row a-text-center', timeout=1):  # Simplistic check for captcha (adjust as needed)
        print('Captcha triggered, skipping...')
        return  # You can add more sophisticated captcha handling here
    
    shipping_info = get_amazon_global_shipping(page)
    df = pd.read_csv(path)
    if asin in df['ASIN'].values:
        df.loc[df['ASIN'] == asin, 'amazon global shipping'] = shipping_info
        df.to_csv(path, index=False)
        print(f'Shipping info updated for {asin}')
    else:
        print(f'ASIN {asin} not found in the file.')

def setup_browser():
    """Set up and return a browser instance."""
    co = ChromiumOptions()
    co.incognito()
    co.headless()
    co.no_imgs()
    return ChromiumPage(co)

def main():
    page = setup_browser()
    path = 'data/amazon/goods_infos.csv'
    
    if not os.path.exists(path):
        print('Target file does not exist.')
        return

    # Read existing ASINs
    df = pd.read_csv(path)
    existing_asins = set(df['ASIN'])
    
    # Read ASINs to process
    with open('data/ASIN.txt', 'r', encoding='utf-8') as f:
        asin_list = [line.strip() for line in f if line.strip() and line.strip() not in existing_asins]

    # Process each ASIN
    for asin in asin_list:
        update_global_shipping(asin, page, path)

    print('Update complete')
    page.quit()

if __name__ == '__main__':
    main()
