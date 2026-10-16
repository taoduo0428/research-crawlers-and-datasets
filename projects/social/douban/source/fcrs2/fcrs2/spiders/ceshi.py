import scrapy
import re
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from fcrs2.items import MovieItem, ReviewItem

def get_movie_urls():
    chromedriver_path = "C://Users//48842//AppData//Local//Google//Chrome//Application//chromedriver.exe"
    options = webdriver.ChromeOptions()
    driver = webdriver.Chrome(service=ChromeService(chromedriver_path), options=options)
    driver.get("https://movie.douban.com/")

    movie_urls = []
    try:
        element = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "/html/body/div[2]/div[2]/div/ul/li[2]/a"))
        )
        element.click()
        time.sleep(5)

        elements = driver.find_elements(By.XPATH, '//*[@id="app"]/div/div[2]/ul/li')
        for i in range(len(elements)):
            element = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, f'//*[@id="app"]/div/div[2]/ul/li[{i + 1}]/a'))
            )
            movie_urls.append(element.get_attribute('href'))
    except Exception as e:
        print(f"出现错误: {e}")
    finally:
        driver.quit()

    print(f"获取到的电影URL列表: {movie_urls}")
    return movie_urls

class UniqueMovieSpider(scrapy.Spider):  # 确保爬虫名称唯一
    name = "unique_movie_spider"
    txt_file_path = "C://Users//48842//Desktop//新建文件夹//movies.txt"

    def start_requests(self):
        movie_urls = get_movie_urls()  # 获取多个电影 URL
        
        self.logger.info(f"将抓取 {len(movie_urls)} 部电影的信息")

        # 初始化 TXT 文件
        with open(self.txt_file_path, 'w', encoding='utf-8') as file:
            file.write("电影信息:\n\n")

        for url in movie_urls:
            yield scrapy.Request(url=url, callback=self.parse, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            })

    def parse(self, response):
        item = MovieItem()
        item['name'] = response.xpath('//div[@id="content"]/h1/span[1]/text()').extract_first()
        item['year'] = re.search(r'\d+', response.xpath('//div[@id="content"]/h1/span[2]/text()').extract_first()).group()
        
        introduce = response.xpath('//*[@id="link-report-intra"]/span/text()').extract_first()
        item['introduce'] = introduce.strip() if introduce else 'N/A'

        director = response.xpath('//div[@id="info"]/span[1]/span/text()').extract_first()
        directorname = response.xpath('//div[@id="info"]//a[@rel="v:directedBy"]/text()').extract_first()
        item['director'] = {director: directorname}

        starring = response.xpath('//div[@id="info"]/span[3]/span[1]/text()').extract_first()
        starrings = response.xpath('//div[@id="info"]//a[@rel="v:starring"]/text()').extract()
        item['starring'] = {starring: starrings}

        screenwriter = response.xpath('//*[@id="info"]/span[2]/span[1]/text()').extract_first()
        screenwriters = response.xpath('//*[@id="info"]/span[2]/span[2]/a/text()').extract()
        item['screenwriter'] = {screenwriter: screenwriters}

        actor_details = []
        litags = response.xpath('//*[@id="celebrities"]/ul/li')
        for li in litags[1:]:
            actor = li.xpath('./div/span[1]/a/text()').extract_first()
            role = li.xpath('./div/span[2]/text()').extract_first()
            actor_detail = {'actor': actor, 'role': role}
            actorlink = li.xpath('./div/span[1]/a/@href').extract_first()
            actor_detail['link'] = actorlink
            actor_details.append(actor_detail)
        item['actors'] = actor_details

        review_url_tail = response.xpath('//*[@id="reviews-wrapper"]/p/a/@href').extract_first()
        if review_url_tail:
            review_url = response.urljoin(review_url_tail)
            yield scrapy.Request(url=review_url, callback=self.reviewparse, meta={'movie_item': item, 'page': 1})
        else:
            self.log("No reviews found for this movie.")
            item['reviews'] = None  # 设置为空值
            yield item  # 返回没有评论的电影

    def reviewparse(self, response):
        movie_item = response.meta['movie_item']
        current_page = response.meta['page']
        reviews = movie_item.get('reviews', [])

        divtags = response.xpath('//div[@class="review-list  "]/div')
        if not divtags:
            self.logger.info("No reviews found for this movie.")
            movie_item['reviews'] = None  # 设置为空值
            yield movie_item  # 返回没有评论的电影
            return

        for div in divtags:
            idname = div.xpath('./div/header/a[2]/text()').extract_first()
            title = div.xpath('./div/div/h2/a/text()').extract_first()
            shortcontent = div.xpath('.//div[@class="short-content"]//text()')

            if shortcontent:
                if len(shortcontent) > 3:
                    shortcontent = shortcontent.extract()[2]
                else:
                    shortcontent = shortcontent.extract_first()
                shortcontent = re.sub(r'\(', '', shortcontent)
                shortcontent = shortcontent.strip()
            else:
                shortcontent = 'N/A'

            review_item = ReviewItem()
            review_item['idname'] = idname
            review_item['title'] = title
            review_item['shortcontent'] = shortcontent
            reviews.append(review_item)

            self.log(f"Extracted review item: {review_item}")

        movie_item['reviews'] = reviews

        next_page = response.xpath('//span[@class="next"]/a/@href').extract_first()
        if next_page and current_page < 5:
            next_page_url = response.urljoin(next_page)
            yield scrapy.Request(url=next_page_url, callback=self.reviewparse, meta={'movie_item': movie_item, 'page': current_page + 1})
        else:
            yield movie_item
