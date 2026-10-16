
# 课题组爬虫源码与数据总仓库

本仓库按原有课题分组整理指定的 **12 个研究目标**：外包 4 站、招聘 3 站、B站、Facebook、Keep、豆瓣、亚马逊+Jiimore 整合项目。项目源文件来源于本机，原目录只读；**仓库应一直保持 Private**。

## 快速导航

| 类别 | 子项目 | 说明 |
|---|---|---|
| 共用引擎 | [外包/招聘源码](projects/market-platform/README.md) | 共享 `drv_crawler`、运行入口、测试与导出工具 |
| 外包 | [四个平台](projects/outsourcing/README.md) | 一品威客、猪八戒、实现网、程序员客栈 |
| 招聘 | [三个平台](projects/recruitment/README.md) | BOSS直聘、前程无忧、猎聘 |
| 视频 | [B站](projects/social/bilibili/README.md) | 视频与字幕、多批次数据 |
| 社交 | [Facebook](projects/social/facebook/README.md) | Android 抓包、批次、反应/分享用户的脱敏研究表 |
| 运动社区 | [Keep](projects/social/keep/README.md) | 第一阶段抓包 JSON、第二阶段结构化处理 |
| 影视社区 | [豆瓣](projects/social/douban/README.md) | 电影与影评 |
| 电商 | [Amazon + Jiimore](projects/commerce/amazon-jiimore/README.md) | 以 ASIN 为主键的整合项目 |

## 首次获取
```bash
git clone https://github.com/taoduo0428/research-crawlers-and-datasets.git
cd research-crawlers-and-datasets
git lfs install
git lfs pull
python tools/verify_release.py
```

数据使用 Git LFS，普通网页上的小文件是指针不是完整数据；需要 `git lfs pull`。GitHub 私有仓库不等于允许公开转发第三方网站的版权内容或用户数据。本仓库已排除私人浏览器状态、认证信息、Facebook 原始 MITM 抓包，且对敏感社交数据进行脱敏。
数据清单与 SHA-256：`metadata/data-manifest.json`；来源及发布副本代码校验：`metadata/source-manifest.json`；限制与遗漏：`docs/SECURITY_AND_LIMITATIONS.md`。所有采集需要遵守所在地规则、平台协议和研究伦理，不允许通过发布的匿名数据尝试重识别个人。

## 研究复现
- 每个目标网站都有单独的 README，并标明其真实数据数量、缺失和最初代码入口。
- 不同批次的文件不直接相加；为学术分析应按 ID/时间关联。
- 请先执行离线验证或单元测试，不要直接用默认配置对网站发起大规模请求。
- 新增采集的本地私密原件必须先完成敏感信息审查再进入仓库。不要提交 .env、真实 Cookie、浏览器 Profile、APK、HAR/PCAP/MITM、认证密钥或尚未获授权的身份数据。
