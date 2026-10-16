
# 豆瓣：电影、演员与影评 Scrapy 实验

`source/fcrs2` 来自桌面 `个人知识库/项目/豆瓣/fcrs2`，可从其中的 `scrapy.cfg` 运行；另保留课程项目早期版本 `source/course-legacy`。需要 `pip install scrapy itemadapter`，进入 `source/fcrs2` 使用 `scrapy list` 查看 Spider，再按需运行 `scrapy crawl <spider-name>`。
`data/movies_anonymized.txt` 是原 `movies.txt` 的去身份副本，电影块 6 个、19 行影评、36 条演员信息，绝非 319 部电影。`data/historical_reviews_text.txt` 是更早课程数据，不能与主表重复相加。
当前 `pipelines.py` 以电影名称去重、覆盖写 `movies.txt`，回调并发可能影响完整性；重新采集前建议使用电影 ID 和流式输出，不要直接覆盖既有成果。
