
# 数据目录与字典使用方法

请先访问各项目 README，再根据 `metadata/data-manifest.json` 的文件级 SHA-256、原始路径与脱敏说明核对数据。

字段含义：
- `record_id`：平台内部记录 ID，用来溯源与去重；不同网站的 ID 不能直接比较。
- `site_name` / `content_category`：来源站点、需求/岗位类型，先过滤再比较价格。
- `description_raw`：采集当时的描述，猪八戒以列表摘要为主。
- `published_at_raw`：原始发布日期，不能用 sitemap 更新时间伪造。
- `manual_review_grade`：一品威客质量等级 A/B/C/D。
- `CID` / `BV号`：B站视频相关标识，字幕按 CID 归并。
- `ASIN` + `日期` / `月份`：电商商品与时间序列联合键。
- `source_post_key` / `actor_key`：仅在本次 Facebook 导出中可关联的不可逆假名；不含原始用户 ID。
- `profile_id`、`post_id`、`comment_id`：Keep 脱敏第一阶段与第二阶段之间可关联的 ID。

大数据存储：`.gitattributes` 对数据表使用 Git LFS；按 `git lfs pull` 下载实际文件。验证 `python tools/verify_release.py` 将检测 hash（LFS 尚未拉取时需先 pull）。
