from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

# 输出文件的路径
output_file_path = r"C:\Users\48842\Desktop\亚马逊\result.txt"

# 设置WebDriver路径
service = Service(executable_path=r"C:\Users\48842\.cache\selenium\chromedriver\win64\123.0.6312.122\chromedriver.exe")
options = webdriver.ChromeOptions()
options.add_argument('--start-maximized')  # 最大化窗口
driver = webdriver.Chrome(service=service, options=options)
wait = WebDriverWait(driver, 20)  # 设置显式等待时间

try:
    # 访问商品列表页面
    driver.get('https://www.jiimore.com/data/product')
    time.sleep(10)  # 提供时间进行页面加载和登录操作（如扫码登录）

    # 获取产品的ASIN和详情页链接
    product_asin_element = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, '.truncate.text-xs.font-medium')))
    product_asin = product_asin_element.text.split('ASIN: ')[1].strip()
    product_name_element = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, 'a.ant-typography')))
    href = product_name_element.get_attribute('href')

    if href:
        product_detail_url = 'https://www.jiimore.com' + href
        print("产品详情URL:", product_detail_url)
        # 访问产品详情页
        driver.get(product_detail_url)
        launch_time_element = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, 'div.flex.items-center.text-sm div.font-medium')))
        launch_time = launch_time_element.text.strip()

        # 点击特定链接
        wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, 'a.ant-typography.truncate.css-g1trec'))).click()
        # 等待页面加载完毕，或进行更多的操作

        # 假设亚马逊链接需要从按钮点击后获取，这里简化为直接构造链接
        amazon_url = f'http://www.amazon.com/dp/{product_asin}'

        # 访问亚马逊页面
        driver.get(amazon_url)
        title = wait.until(EC.visibility_of_element_located((By.ID, 'productTitle'))).text.strip()
        rating = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, 'i.a-icon-star'))).text.strip()
        rating_count = wait.until(EC.visibility_of_element_located((By.ID, 'acrCustomerReviewText'))).text.strip()
        price = wait.until(EC.visibility_of_element_located((By.ID, 'priceblock_ourprice'))).text.strip()

        # 将信息写入文件
        with open(output_file_path, 'w', encoding='utf-8') as file:
            file.write(f"ASIN: {product_asin}\n")
            file.write(f"上架时间: {launch_time}\n")
            file.write(f"标题: {title}\n")
            file.write(f"评分: {rating}\n")
            file.write(f"评分数量: {rating_count}\n")
            file.write(f"价格: {price}\n")
    else:
        print("未找到产品链接的href属性。")
        print("获取的ASIN:", product_asin)


finally:
    driver.quit()  # 无论如何都确保关闭浏览器
