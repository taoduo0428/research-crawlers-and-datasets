# 这些数据怎么读

如果你只是想分析数据，不用先研究爬虫代码。找到对应网站的 `data/`，下载文件，然后先看表头、主键和缺失情况。

## 文件先分清楚

- `final.csv`：通常已经清洗过，列比较少，适合直接做基础分析。
- `full-source.csv`：保留更多采集时的字段，查重、查来源时更有用。
- `legacy`、`supplement` 或同项目下的多个筛选表：可能是旧版、补充版或主表子集。**不要把行数简单相加。**
- `metadata/data-manifest.json`：每个发布文件的路径、大小、SHA-256，以及有记录数时的记录数。怀疑下载不全，先看它。

## 常用字段

| 字段 | 用来做什么 | 注意 |
| --- | --- | --- |
| `record_id` | 外包任务、岗位的原始记录 ID | 不同网站之间可能撞号，最好加上网站名 |
| `description_raw` / `JD` | 任务或岗位的文字描述 | 有些网站只有列表摘要，不是完整详情 |
| `published_at_raw` | 页面返回的发布日期 | 不要拿网站地图的 `lastmod` 代替真正发布日期 |
| `budget_raw` / `薪资` | 报价或薪酬原文 | 范围、单位、协商价、平台估价要分开处理 |
| `content_category` | 区分外包任务和全职岗位 | 实现网两种类型都有 |
| `manual_review_grade` | 一品威客审核等级 | A/B 是已筛出的子集，不是另一批采集结果 |
| `CID` / `BV号` | B站的视频/字幕标识 | 合并字幕时主要看 CID |
| `ASIN` | Amazon 商品标识 | 联合月数据时还要匹配月份 |
| `profile_id` / `post_id` / `comment_id` | Keep 三张表的匿名关联键 | 不能还原到真实用户名 |
| `source_post_key` / `actor_key` | Facebook 匿名帖子—用户关系 | 只适合这次发布数据内的关联 |

## 合并时按什么键

招聘和外包尽量用「网站 + 原始 ID」；B站用 CID；Amazon 的月数据用「ASIN + 月份」；Keep 先用 `author_id` 对 `profile_id`，再用 `post_id` 对评论。某份表没有关键字段，就先标注「无法直接溯源」，不要靠行号硬拼。

## Git LFS 怎么办

大型 CSV 等文件使用 Git LFS。克隆后运行：

```bash
git lfs pull
python tools/verify_release.py
```

看到 `missing=0` 和 `mismatched=0` 才说明文件已拿全、没损坏。GitHub 网页上显示的 LFS 指针不是数据本身。

最后提醒一句：采集数据有缺失和历史版本差异，表中数字也不代表当前网站的实时情况。每次做图或写论文，记上数据批次和筛选口径。
