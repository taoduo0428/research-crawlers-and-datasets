# Facebook：帖子 Reaction 和分享采集

这个工程比其他旧爬虫复杂一些：它用 Android 模拟器打开帖子，通过本地 API 管理任务，采集当前登录账号能看到的 Reaction/分享信息。

## 从哪读起

- Windows：[`source/WINDOWS_DEPLOYMENT.md`](source/WINDOWS_DEPLOYMENT.md)
- macOS：[`source/macos/QUICKSTART.md`](source/macos/QUICKSTART.md)
- 程序入口和整体结构：[`source/README.md`](source/README.md)
- API 怎么提交和查任务：[`source/API_USAGE.md`](source/API_USAGE.md)

主要代码在 `source/facebook_capture_api.py`（本地任务接口）、`source/facebook_android_capture.py`（设备采集）、`source/analyze_facebook_capture.py`（分析历史产物）。`source/tests/` 是测试，`web/`、`web_v2/` 是两版前端。

**真实运行不是安装一个 Python 包就结束了。** 还要按部署手册准备 Android 环境、人工登录、网络和工具依赖。这里没有提供别人的 APK、Cookie、证书或账号资料。

## 仓库中能用的数据

`data/sanitized_reactions.csv` 和 `data/sanitized_shares.csv` 保留匿名帖子与行为者的关系，不含用户真实 ID、姓名、正文和登录信息。Reaction 表是历史多批次合并结果，并不等于某一个帖子的一次完整抓取。

历史任务记录中，有不少 `failed`、`cancelled`。即使写着 `completed`，也要再看目标帖子是否一致、当前账号能看见多少人，以及分页是否真正结束。不要只看任务状态就认定全量完成。

真实抓包 `runs/`、MITM Flow、模拟器磁盘、原始用户信息都没有放进仓库。相关资料请在获授权的本机环境里保管。
