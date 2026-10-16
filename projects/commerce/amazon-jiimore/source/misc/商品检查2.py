from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import chromedriver_autoinstaller
import pandas as pd
import time
from selenium.common.exceptions import NoSuchElementException, TimeoutException

# 自动检测并安装ChromeDriver
chromedriver_autoinstaller.install()

# 设置Selenium WebDriver
service = Service(executable_path=r"C:\Users\48842\.cache\selenium\chromedriver\win64\123.0.6312.122\chromedriver.exe")
driver = webdriver.Chrome(service=service)

# 要抓取的亚马逊商品页面URL
url = "https://www.amazon.com/-/zh/dp/0316572063/ref=bmx_dp_bm12uf1n_d_sccl_2_1/132-9704293-1312359?pd_rd_w=LJNqv&content-id=amzn1.sym.628e3119-8afd-4af2-be6b-18415b51e649&pf_rd_p=628e3119-8afd-4af2-be6b-18415b51e649&pf_rd_r=R1118D42B73BER4EDR11&pd_rd_wg=Zhagv&pd_rd_r=cc72c07a-bd20-4478-bc1f-58b04ed2a48f&pd_rd_i=0316572063&psc=1"

try:
    driver.get(url)
    wait = WebDriverWait(driver, 10)
    
    # 创建一个空字典，用于存储商品信息
    product_info = {}

    # 获取商品标题
    title = wait.until(EC.presence_of_element_located((By.ID, "productTitle"))).text.strip()
    product_info["Title"] = title

    # 获取评分
    rating = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "i.a-icon-star span"))).text
    product_info["Rating"] = rating

    # 获取评分数
    rating_count = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "span#acrCustomerReviewText"))).text
    product_info["Rating Count"] = rating_count

    # 检查是否存在标签
    try:
        tag = driver.find_element(By.CSS_SELECTOR, "span.ac-for-text span").text
    except NoSuchElementException:
        tag = "No tag found"
    product_info["Tag"] = tag

    # 尝试获取价格
    try:
        price = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'span.a-price > span.a-offscreen'))).text
    except NoSuchElementException:
        price = "Price not found"
    product_info["Price"] = price

    # 尝试获取商品介绍
    try:
        description_button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "#bookDescription_feature_div .a-expander-prompt")))
        description_button.click()
        description = wait.until(EC.visibility_of_element_located((By.ID, "bookDescription_feature_div"))).text
    except TimeoutException:
        description = "No description found"
    product_info["Description"] = description

    # 尝试获取商品属性信息
    try:
        product_details_button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "a.a-expander-prompt")))
        product_details_button.click()
        product_details = wait.until(EC.visibility_of_element_located((By.ID, "detailBullets_feature_div"))).text
    except (NoSuchElementException, TimeoutException):
        product_details = "No product details found or unavailable"
    product_info["Product Details"] = product_details

    # 尝试获取AI评论总结，如果存在
    try:
        ai_summary = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "div#ai_reviews_summary"))).text
        product_info["AI Summary"] = ai_summary

        # 如果存在AI标签评论
        ai_tags = driver.find_elements(By.CSS_SELECTOR, "div#ai_reviews_tags div.ai_tag")
        for tag_element in ai_tags:
            tag_element.click()  # 点击标签
            time.sleep(1)  # 等待评论加载
            ai_review_content = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "div.ai_reviews_content"))).text
            product_info[f"AI Review for {tag_element.text}"] = ai_review_content
    except TimeoutException:
        product_info["AI Summary"] = "No AI reviews summary found or timed out waiting for it."
    except NoSuchElementException:
        product_info["AI Summary"] = "No AI tags found."

    # 将字典转换为DataFrame
    df = pd.DataFrame.from_dict(product_info, orient="index").transpose()

    # 保存DataFrame到Excel文件
    output_path = r"C:\Users\48842\Desktop\网络爬虫\results.xlsx"
    df.to_excel(output_path, index=False)

    print("信息已保存到Excel文件！")

finally:
    driver.quit()  # 关闭浏览器，结束会话
