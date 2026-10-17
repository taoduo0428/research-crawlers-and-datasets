# 测试目录

这里放的是项目测试。刚接手时建议先跑与你要改的文件直接相关的那几项，不要把测试通过当作「真实 Facebook 抓取也通过」。需要设备和网络的测试先看清楚条件再启动。
本目录集中存放自动化测试；`manual/` 是两份独立 PowerShell XML 夹具测试，不会自动执行模拟器操作。业务源码仍在项目根目录。

所有命令从项目根目录运行。

```powershell
# Windows 全套
.venv\Scripts\python.exe -B -m unittest discover -s tests -t .
# 单模块
.venv\Scripts\python.exe -B -m unittest tests.test_facebook_capture_api
# 两份独立 XML 夹具
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File tests/manual/test_reel_reaction_selector.ps1
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File tests/manual/test_reel_reaction_sheet_state.ps1
```

```sh
# macOS 全套（Windows 专属测试按既有条件跳过）
.venv/bin/python -B -m unittest discover -s tests -t .
# 单模块
.venv/bin/python -B -m unittest tests.test_facebook_api_client
```

根目录执行 `python -m unittest discover` 仍可发现全部 Python 测试。原来的 `python -m unittest test_xxx` 改用 `python -m unittest tests.test_xxx`；不保留根目录占位文件。不要直接运行 `python tests/test_xxx.py`，以免项目模块搜索路径不同。

测试里的 `ROOT`、`BASE` 等仍指向项目根目录；测试间导入用 `tests.test_xxx`。历史记录中的旧命令只是当时快照，当前命令以本说明、项目 README 和 CI 为准。

测试通过不代表真实 Facebook 名单抓取、当前登录态或真实 macOS 设备已验收。
