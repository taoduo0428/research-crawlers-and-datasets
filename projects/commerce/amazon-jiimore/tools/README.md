# 历史研究数据的辅助工具

本目录的工具针对 **`../data/` 的七张已整理历史表**，并不是新版 `../source/` 的在线采集主程序。本次发布保留旧工具和对应测试。

| 工具 | 作用 |
| --- | --- |
| `review_data.py` | 审计七表、按 ASIN+月份合并销量与留评率、按 ASIN 分别导出七份记录；冲突保留为明细，不偷偷覆盖 |
| `live_probe.py` | 原仓库的限速、无凭据公开接口小样本检查；不读取新整理版的本地账号文件，也不会写历史七张表 |
| `check_release.py` | 检查本次公开发布允许的源码清单、隐私文件未被追踪、ASIN 格式、README 相对链接和七张历史表是否保留；不联网 |

从仓库根目录运行：

```bash
python projects/commerce/amazon-jiimore/tools/review_data.py audit
python projects/commerce/amazon-jiimore/tools/review_data.py merge-monthly --output ../amazon_monthly.csv
python projects/commerce/amazon-jiimore/tools/review_data.py export-asin --asin B09XMYFTB7 --outdir ../amazon_one_asin
python projects/commerce/amazon-jiimore/tools/live_probe.py --asin B005G9E98O
python projects/commerce/amazon-jiimore/tools/check_release.py
```

`live_probe.py` 默认只读取公开可访问接口，不会继承 Edge 登录态；其先前真实结果在 [历史联网实测记录](../docs/联网实测_2026-10-17.md)。这不能替代本次新版源码的实际登录态验收。

需要修改数据时，请先保存原始层、在仓库外生成新的研究成果，并更新发布清单；不要直接改历史原件。