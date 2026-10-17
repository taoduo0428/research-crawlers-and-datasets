# 豆瓣：电影和影评的小项目

这个是早期的 Scrapy 练习项目，别拿它当成熟的全站电影库。

## 现在有哪些文件

- `source/fcrs2/`：主要的 Scrapy 项目，里面有 `scrapy.cfg`、Spider、Items、Pipelines。
- `source/course-legacy/`：更早的课程实验残留代码，留着查历史。
- `data/movies_anonymized.txt`：**6 部电影**，含 19 行影评和 36 行演员信息。
- `data/historical_reviews_text.txt`：课程时期的另一份文本，不能直接和主表相加。

以前 `movies.txt` 虽然有 319 行非空文本，但其中大部分是演员和影评，不是 319 部电影。

## 想跑代码

先进入 `source/fcrs2/`，准备 Python 虚拟环境，安装 `scrapy` 和 `itemadapter`，再执行 `scrapy list` 看有什么爬虫。当前检查机器没有装 Scrapy，因此这次没有实测连接豆瓣。

历史 `pipelines.py` 用电影名称合并，而且会覆盖写结果文件。重新抓数据前最好先备份，并改成按电影 ID 持续写入，避免并发回调时遗漏信息。
