import requests
from bs4 import BeautifulSoup

# The path to the output file on your desktop
output_file_path = r"C:\Users\48842\Desktop\亚马逊\result.txt"

# Start a session to maintain cookies between requests
session = requests.Session()

# Your user agent string
headers = {
     'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36'
}

# Function to get product ASIN and name element
def get_product_info(product_list_url):
    response = session.get(product_list_url, headers=headers)
    soup = BeautifulSoup(response.content, 'html.parser')
    product_asin = soup.select_one('.truncate.text-xs.font-medium').get_text(strip=True).split(': ')[1]
    product_name_element = soup.select_one('a.css-r8or8k')
    return product_asin, product_name_element

# Function to get detailed info from the product detail page
def get_product_details(product_detail_url):
    response = session.get(product_detail_url, headers=headers)
    soup = BeautifulSoup(response.content, 'html.parser')
    # You would need to add the actual code to scrape the details here
    # ...
    return {}

# Assuming the first product is what you want
product_list_url = 'https://www.jiimore.com/data/product'
product_asin, product_name_element = get_product_info(product_list_url)

# Get the detail URL from the product name element
product_detail_url = 'https://www.jiimore.com' + product_name_element['href']
product_details = get_product_details(product_detail_url)

# Write data to the file
with open(output_file_path, 'w', encoding='utf-8') as file:
    file.write(f"ASIN: {product_asin}\n")
    # Write other details to the file
    # ...

# Remember to close the session when done
session.close()
