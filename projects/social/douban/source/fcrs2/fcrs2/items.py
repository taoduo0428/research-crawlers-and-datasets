

import scrapy


class Fcrs2Item(scrapy.Item):
    # define the fields for your item here like:
    # name = scrapy.Field()
    pass
class MovieItem(scrapy.Item):
    name = scrapy.Field()
    year = scrapy.Field()
    introduce = scrapy.Field()
    director = scrapy.Field()
    starring = scrapy.Field()
    screenwriter = scrapy.Field()
    
    actors_details = scrapy.Field()
    reviews = scrapy.Field()


class ActorItem(scrapy.Item):
    actor = scrapy.Field()
    role = scrapy.Field()
    gender = scrapy.Field()
    birthday = scrapy.Field()
    birthplace = scrapy.Field()
    job=scrapy.Field()
    IMDb=scrapy.Field()

class ReviewItem(scrapy.Item):
    idname = scrapy.Field()
    title = scrapy.Field()
    shortcontent = scrapy.Field()
