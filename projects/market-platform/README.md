# 外包、招聘共用的爬虫代码

外包四站和招聘三站的主要代码都在这里。这样放是因为它们共用不少功能：发请求、浏览器操作、分类、分页、断点保存和导出。网站自己的数据另放在 `../outsourcing/` 和 `../recruitment/`，不要在这里找最终 CSV。

## 先从哪里看

- `run.py`：统一入口。拿不准命令时，先运行 `python run.py --help`。
- `drv_crawler/sites.py`：一品威客、猪八戒、实现网、程序员客栈的适配器。
- `drv_crawler/recruitment.py`：招聘的通用代码。
- `drv_crawler/boss_cdp/`、`job51/`、`liepin/`：三个招聘网站各自的子工程。
- `drv_crawler/models.py`、`exporters.py`：记录格式与输出。
- `tools/`：合并、补详情、清洗、检查和打包；有些脚本是针对旧采集结果的。
- `tests/`：单元测试和固定页面样例。
- `docs/`：旧的交接说明、失败原因和采集记录。里面的旧路径不一定在这份发布仓库中还存在。

## 在 Windows 上怎么开始

进入本文件夹后：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --help
```

如果 `requirements.txt` 与某个招聘子工程的依赖不同，以相应子工程 README 为准。需要浏览器、登录或代理的步骤请在自己的机器上配置，不要复制别人浏览器的登录目录。

真正开始采集前，先看所选网站的参数说明并做一次小样本试跑。`tests/` 中包含很多可以离线检查的逻辑，但也可能有依赖真实环境的测试，不建议不加筛选就跑完整套件。

历史的 `crawler.py` 与 `run.py` 不完全是一回事。接手项目优先看当前统一入口，只有要查旧脚本才回看其他文件。

[回到总目录](../../README.md) · [外包数据](../outsourcing/README.md) · [招聘数据](../recruitment/README.md)
