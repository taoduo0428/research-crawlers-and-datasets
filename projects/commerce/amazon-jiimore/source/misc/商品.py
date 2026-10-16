from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import chromedriver_autoinstaller
import pandas as pd
from bs4 import BeautifulSoup
import time
from selenium.common.exceptions import NoSuchElementException, TimeoutException

# 自动检测并安装ChromeDriver
chromedriver_autoinstaller.install()

# 设置Selenium WebDriver
service = Service(executable_path=r"C:\Users\48842\.cache\selenium\chromedriver\win64\123.0.6312.122\chromedriver.exe")
driver = webdriver.Chrome(service=service)

# 要抓取的亚马逊商品页面URL
url = "https://www.amazon.com/dp/B08GB3QC1H"

try:
    driver.get(url)
    wait = WebDriverWait(driver, 10)
    
    # 创建一个空字典，用于存储商品信息
    product_info = {}

    # 获取商品标题
    title = wait.until(EC.presence_of_element_located((By.ID, "productTitle"))).text.strip()
    product_info["Title"] = title

    try:
       rating_element = WebDriverWait(driver, 20).until(
         EC.visibility_of_element_located((By.CSS_SELECTOR, "i.a-icon.a-icon-star.a-star-4-5.cm-cr-review-stars-spacing-big"))
    )
    # 从类名中提取评分数字，类名格式为 "a-star-4-5"
       rating_class = rating_element.get_attribute("class")
    # 用正则表达式找到评分数值
       import re
       match = re.search(r'a-star-(\d+)-(\d+)', rating_class)
       if match:
        rating_score = float(f"{match.group(1)}.{match.group(2)}")
       else:
        rating_score = None  # 如果没有匹配，设为None
       product_info["Rating"] = rating_score
    except TimeoutException:
       print("等待评分元素超时，可能页面加载过慢或元素选择器不正确。")
       product_info["Rating"] = "评分加载失败"
    except NoSuchElementException:
       print("没有找到评分元素，检查选择器是否正确。")
       product_info["Rating"] = "评分未找到"
    except ValueError:
       print("提取评分时无法转换为浮点数，检查提取逻辑。")
       product_info["Rating"] = "评分数据错误"
    except Exception as e:
       print(f"处理评分时发生未知错误: {e}")



    # 检查是否存在标签
    try:
        tag = driver.find_element(By.CSS_SELECTOR, "span.ac-for-text span").text
    except NoSuchElementException:
        tag = "No tag found"
    product_info["Tag"] = tag

    # 尝试获取价格
    try:
        price_html = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'span.a-price'))).get_attribute('innerHTML')
        price_soup = BeautifulSoup(price_html, 'html.parser')
        price = price_soup.get_text(separator=' ', strip=True)
    except NoSuchElementException:
        price = "Price not found"
    product_info["Price"] = price

    # 尝试获取商品介绍
    try:
        description_elements = driver.find_elements(By.CSS_SELECTOR, "ul.a-unordered-list.a-vertical.a-spacing-mini > li.a-spacing-mini")
        descriptions = []
        for element in description_elements:
            text = element.find_element(By.CSS_SELECTOR, "span.a-list-item").text.strip()
            descriptions.append(text)
        product_info["Description"] = " ".join(descriptions)  # 将所有描述合并为一个字符串
    except NoSuchElementException:
        product_info["Description"] = "未找到描述"
    except Exception as e:
        print(f"在抓取商品描述时发生错误: {e}")
    # 尝试获取商品介绍信息
    try:
        attributes_rows = driver.find_elements(By.CSS_SELECTOR, "tr.a-spacing-small")
        for row in attributes_rows:
            attribute_name = row.find_element(By.CSS_SELECTOR, "td.a-span3 span").text.strip()
            attribute_value = row.find_element(By.CSS_SELECTOR, "td.a-span9 span").text.strip()
            product_info[attribute_name] = attribute_value
    except NoSuchElementException:
        print("未找到商品介绍信息。")
    except Exception as e:
        print(f"在抓取商品介绍时发生错误: {e}")
    # 获取总的AI评论总结
    try:
        main_summary = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "p.a-spacing-small")))
        product_info["Main AI Summary"] = main_summary.text.strip()
        print("Main AI Summary fetched successfully.")
    except TimeoutException:
        print("Failed to load main AI summary due to timeout.")
        product_info["Main AI Summary"] = "Failed to load main AI summary due to timeout."

    # 获取并操作每一个标签
    buttons = driver.find_elements(By.CSS_SELECTOR, "button._cr-product-insights_style_aspect-button__1BFiu")
    print(f"Found {len(buttons)} buttons.")

    for button in buttons:
        aspect_name = button.find_element(By.CSS_SELECTOR, "span.a-size-base").text.strip()
        print(f"Working on button: {aspect_name}")
        
        # 点击按钮
        driver.execute_script("arguments[0].click();", button)
        time.sleep(3)  # 等待AJAX内容加载

        # 抓取对应的AI总结
        try:
            # 修改这里的选择器，确保它能正确定位到更新的内容
            summary_element = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div._cr-product-insights_style_insight-bottom-sheet-content__1KIl7 div.a-section.a-spacing-base p")))
            product_info[f"AI Summary for {aspect_name}"] = summary_element.text.strip()
            print(f"AI Summary for {aspect_name}: {summary_element.text.strip()}")
        except TimeoutException:
            print(f"Failed to load AI summary for {aspect_name} due to timeout.")
            product_info[f"AI Summary for {aspect_name}"] = "Failed to load AI summary due to timeout."
        except Exception as e:
            print(f"Error fetching AI summary for {aspect_name}: {str(e)}")
            product_info[f"AI Summary for {aspect_name}"] = f"Error: {str(e)}"

    


    # 将字典转换为DataFrame
    df = pd.DataFrame.from_dict(product_info, orient="index").transpose()

    # 保存DataFrame到Excel文件
    output_path = r"C:\Users\48842\Desktop\网络爬虫\results.xlsx"
    df.to_excel(output_path, index=False)

    print("信息已保存到Excel文件！")

finally:
    driver.quit()  # 关闭浏览器，结束会话
