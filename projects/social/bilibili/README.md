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
python projects/social/bilibili/tools/collect_combined.py --bvid BV1YV4y1p76c --bvid BV1th411s7Tf --output bili_two.csv

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

2026-10-17 的测试中，用 5 条以前抓到过字幕的真实视频跑了完整脚本：

- 生成了 `data/combined_verified_samples.csv`，**5 条都有字幕正文**；
- 其中 **4 条成功刷新了 B站当前公开的视频信息**，另外 1 条沿用历史视频信息；
- 5 条字幕全部来自历史 CSV，**不是当日重新下载的字幕**；
- B站目前的匿名字幕接口仍返回空轨道；带之前提供的设备 Cookie 也没有变成登录状态。

因此「旧字幕和新视频信息合表」已经通过真实测试；「今天新抓到字幕」**还没通过真实验证**。要核实后者，需在自己的机器上使用已登录的 B站会话。脚本可以通过本机环境变量 `BILI_AUTH` 使用会话值，**不要发到聊天里，也不要写进源码或提交 Git**。没有会话时程序仍能工作，但可能只能从历史数据补字幕。

新版代码同时检查有签名的播放器接口及新的 Protobuf 字幕元数据接口，不再只根据旧接口的空数组判定「无字幕」。接口变化时也不会下载视频或音频。

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
| `data/combined_verified_samples.csv` | 新程序跑出来的 5 条真实视频合并示例 |
| `data/第一次字幕/merged_unique.csv` | 第一批视频信息与字幕对照表 |
| `data/supplement/`、其他 `data/*.csv` | 键盘、手环等主题及补充数据 |
| `source/main/`、`source/supplement/` | 历史采集脚本，主要用来查以前的写法 |
| `tools/check_bilibili.py` | 检查几个历史数据文件的 BV/CID 是否对应，不改数据 |
| `tools/collect_combined.py` | **现在推荐使用的统一视频信息+字幕工具** |

两批字幕按 BV/CID 合并后共有 **4,904 个不同视频片段**，至少 **3,667 个片段有历史字幕**。同一 CID 有时有不同版本的字幕，分析时要保留来源和批次，别直接按行号拼。

有些旧文件放错了位置：`source/main/封面/图片.py` 实际是 Amazon 页面解析代码，不是 B站工具；`main/3.py` 和 `supplement/字幕(csv).py` 是重复脚本。保留它们是为了查历史，新项目不建议从这些文件启动。

**使用范围：** 字幕、视频简介等属于第三方内容，公开重新分发前需要确认权限。这个工具只取公开元数据与授权后可访问的字幕轨道，不提供绕过登录的能力。
