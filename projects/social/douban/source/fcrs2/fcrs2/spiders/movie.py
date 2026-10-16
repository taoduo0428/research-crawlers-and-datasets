import re
import scrapy
from fcrs2.items import MovieItem, ReviewItem
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
from webdriver_manager.chrome import ChromeDriverManager

class MovieSpider(scrapy.Spider):
    name = "movie"

    def __init__(self, movie_urls=None, *args, **kwargs):
        super(MovieSpider, self).__init__(*args, **kwargs)
        if movie_urls is None:
            self.start_urls = self.get_movie_urls()
        else:
            self.start_urls = movie_urls.split(',')

    def get_movie_urls(self):
        
        options = webdriver.ChromeOptions()
        driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=options)
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
            self.logger.error(f"出现错误: {e}")
        finally:
            driver.quit()

        self.logger.info(f"获取到的电影URL列表: {movie_urls}")
        return movie_urls

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
        
        litags = response.xpath('//*[@id="celebrities"]/ul/li')
        actors_details = []
        item['actors_details'] = actors_details  

        for li in litags:
            actor = li.xpath('./div/span[1]/a/text()').extract_first()
            role = li.xpath('./div/span[2]/text()').extract_first()

         
            if not any(d['actor'] == actor for d in actors_details):
                actor_item = {
                    'actor': actor,
                    'role': role,
                }

                actorlink = li.xpath('./div/span[1]/a/@href').extract_first()
                self.log(f"Actor Link: {actorlink}")
                request = scrapy.Request(url=actorlink, callback=self.actordetail)
                request.meta['actor_item'] = actor_item
                request.meta['actors_details'] = actors_details
                request.meta['movie_item'] = item
                yield request
        
        review_url_tail = response.xpath('//*[@id="reviews-wrapper"]/p/a/@href').extract_first()
        if review_url_tail:
            review_url = response.urljoin(review_url_tail)
            yield scrapy.Request(url=review_url, callback=self.reviewparse, meta={'movie_item': item, 'page': 1})
        else:
            self.log("No reviews found for this movie.")
            item['reviews'] = None 
            yield item  

    def actordetail(self, response):
        actor_item = response.meta['actor_item']
        actors_details = response.meta['actors_details']
        movie_item = response.meta['movie_item']
        
        litags = response.xpath('//ul[@class="subject-property"]/li')
        self.log(f"Number of detail li tags found: {len(litags)}")
        for litag in litags:
            name = litag.xpath('./span[1]/text()').extract_first()
            if name:
                name = re.search(r'[\u4e00-\u9fa5]+', name)
                if name:
                    name = name.group()
                else:
                    name = "N/A"
            else:
                name = "N/A"

            content = litag.xpath('./span[2]/text()').extract_first()
            if content:
                content = content.strip()
            else:
                content = "N/A"

            actor_item[name] = content  # 动态添加字段

        self.log(f"Extracted actor item: {actor_item}")
        actors_details.append(actor_item)
        movie_item['actors_details'] = actors_details

        # 确保所有演员信息都已提取完毕
        yield movie_item

    def reviewparse(self, response):
        movie_item = response.meta['movie_item']
        current_page = response.meta['page']
        reviews = movie_item.get('reviews', [])

        divtags = response.xpath('//div[@class="review-list  "]/div')
        if not divtags:
            self.logger.info("No reviews found for this movie.")
            movie_item['reviews'] = None  
            yield movie_item  
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
