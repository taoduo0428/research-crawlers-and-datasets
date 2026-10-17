# Keep：先抓包，再整理数据

这个项目有两个阶段，**不是一个脚本从头跑到尾**。

## 第一阶段：抓包

之前在本机拿到 101 份用户资料 JSON 和 105 份动态 JSON。原始数据里有账号信息、内容和链接，不适合直接共享，所以 GitHub 上的 `data/phase1_anonymized/` 放的是**只保留少量研究字段的匿名摘要**：

- `profiles/`：101 份用户资料摘要；
- `activities/`：105 份动态摘要。

这些文件能用来做后续关联分析，但**不能还原完整抓包响应**。目前也没有找到一份能让其他同学在新机器上一键重做网络抓包的完整程序；历史采集器的快捷方式不等于安装包。

## 第二阶段：把 JSON 整理成表格

第二阶段不需要登录 Keep，也不用联网。在**总仓库根目录**运行：

```bash
python tools/normalize_keep.py --input projects/social/keep/data/phase1_anonymized --output ../keep-rebuilt
```

对应的数据放在 `data/phase2/`：

| 文件 | 记录数 | 内容 |
| --- | ---: | --- |
| `profiles.csv` | 100 | 匿名用户及少量属性 |
| `posts.csv` | 1,050 | 按动态 ID 去重后的动态 |
| `comments.csv` | 1,692 | 示例评论的关联索引（不含评论正文） |

三个表通过用户 ID、动态 ID 关联。历史 CSV 曾把动态和评论放在同一张表，一共 2,838 行，**不能理解为 2,838 条不同动态**。

`tools/normalize_keep.py` 已加入固定排序和稳定匿名 ID 的处理，从当前匿名 JSON 重新生成三张表应与仓库中的版本逐字节一致。需要验证就运行 `python -m unittest discover -s tests -p test_keep_release.py`。

## 哪些东西只用于查历史

`source/phase2/` 有以前的整理脚本，使用旧的本机路径，直接运行可能不合适。课题组成员要重做第二阶段，请用上面的仓库级工具。

另外，2026-10-17 修过一轮匿名数据：之前版本的 ID 不要与现在的 ID 混用。旧 Git 历史还没有独立清理完，准备公开传播数据时必须额外检查。
