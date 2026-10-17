# B站：视频信息和字幕放在一张表里

这里以前分了好几批，脚本也有重复。**现在建议直接用 `tools/collect_combined.py`**：输入 BV 号，取当前视频信息，查字幕，最后输出一张 CSV。以前抓到的字幕如果当前接口拿不到，会从已有数据里补上，但会明确写「历史CSV」，不会算作这次新抓到的。

目前先不处理封面图片。

## 直接试一下

在**总仓库根目录**打开终端，确保已经安装 `requests`：

```powershell
python -m pip install requests

# 从历史上有字幕的视频中挑5条，获取当前视频信息，合并字幕
python projects/social/bilibili/tools/collect_combined.py --limit 5 --output bili_result.csv

# 指定真实BV号（可以重复传入 --bvid）
python projects/social/bilibili/tools/collect_combined.py --bvid BV1TgjwzGEuG --bvid BV15qQwB4EZ9 --output ../bili_current.csv

# 不联网，只整理历史视频信息和已有字幕
python projects/social/bilibili/tools/collect_combined.py --offline --limit 5 --output bili_offline.csv
```

`bili_result.csv` 在你当前执行命令的文件夹里，不会覆盖原始数据。

输出保留了旧表的 **19 列视频信息**（BV、AV、CID、UP 主、标题、分类、发布日期、简介、播放/点赞/收藏等），另外增加了 `字幕`、`字幕来源`、`字幕状态`、`字幕语言`、`字幕片段数`、`视频信息来源`、`采集时间` 和 `备注`，总共 **27 列**。

**怎么看字幕来源：**

- `实时接口`：这次从 B站真正下载到字幕正文。
- `历史CSV：第一次字幕/结果.csv` 或 `历史CSV：结果.csv`：本次在线没拿到字幕，用以前抓到的正文补全。
- `字幕状态` 写着 `anonymous_no_tracks_or_login_required`：当前未登录请求没有字幕轨道，**不代表这个视频一定没有字幕**。

## 真视频测试到哪一步了

2026-10-17 从 B站找了 10 条真实视频来测，**10 条视频信息都成功获取，5 条至少一次返回字幕轨道**。随后选其中 5 条跑完整 CSV 流程：

- **5/5 条成功获取当前视频信息**（标题、UP 主、播放、点赞等）。
- **3/5 条成功从当前 B站接口下载字幕正文**，分别有 2,703、231、1,790 段。
- 另外 2 条本轮没有取到字幕轨道；有一条在单独测试中曾经成功，说明有时请求结果会变，不能承诺每次都有字幕。
- 实时数据输出了 27 列 CSV，测试时在临时目录保存，**没有把新抓的第三方字幕原文上传到公开仓库**。

详细视频链接、数据量和修复原因见 [真实视频测试记录](docs/REAL_VIDEO_TEST_2026-10-17.md)。

这次主要修了两个真实错误：新版 Protobuf 字幕信息外层还有一层 `data` 包装；此外字幕地址需要按播放器的编码格式转换成实际 CDN 地址，不能直接请求 `subtitle.bilibili.com`。**两个问题修好后，不带 Cookie 也能抓到部分公开视频的字幕。**

没有字幕时仍可尝试从旧 CSV 合并，但会明确标注 `历史CSV`，不会冒充本次实时采集。部分视频可能要求有效登录状态；确需配置时，只能在自己的电脑设置 `BILI_AUTH` 环境变量，**不要把 Cookie 发到聊天或提交 Git**。

测试命令：

```bash
python -m unittest discover -s projects/social/bilibili/tests -p test_combined.py
python projects/social/bilibili/tools/check_bilibili.py
```

## 原来的文件怎么找

| 文件夹或文件 | 具体内容 |
| --- | --- |
| `data/第一次字幕/结果.csv` | 第一批 3,259 行，2,507 行有字幕 |
| `data/结果.csv` | 第二批 3,132 行，其中 1,836 行有字幕，存在重复 CID |
| `data/空.csv` | 第二批中 1,296 行字幕为空的记录，已修正早期编码问题 |
| `data/combined_verified_samples.csv` | 前一轮 5 条历史字幕合并示例，**不是这次的新字幕下载结果** |
| `data/第一次字幕/merged_unique.csv` | 第一批视频信息与字幕对照表 |
| `data/supplement/`、其他 `data/*.csv` | 键盘、手环等主题及补充数据 |
| `source/main/`、`source/supplement/` | 历史采集脚本，主要用来查以前的写法 |
| `tools/check_bilibili.py` | 检查几个历史数据文件的 BV/CID 是否对应，不改数据 |
| `tools/collect_combined.py` | **现在推荐使用的统一视频信息+字幕工具** |

两批字幕按 BV/CID 合并后共有 **4,904 个不同视频片段**，至少 **3,667 个片段有历史字幕**。同一 CID 有时有不同版本的字幕，分析时要保留来源和批次，别直接按行号拼。

有些旧文件放错了位置：`source/main/封面/图片.py` 实际是 Amazon 页面解析代码，不是 B站工具；`main/3.py` 和 `supplement/字幕(csv).py` 是重复脚本。保留它们是为了查历史，新项目不建议从这些文件启动。

**使用范围：** 字幕、视频简介等属于第三方内容，公开重新分发前需要确认权限。这个工具只取公开元数据与授权后可访问的字幕轨道，不提供绕过登录的能力。
