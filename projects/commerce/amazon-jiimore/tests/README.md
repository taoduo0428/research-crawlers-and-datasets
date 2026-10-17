# 历史数据与只读探针测试

这里的 `test_review_data.py` / `test_live_probe.py` 测试的是项目 `tools/` 下的历史数据审计与只读网络探针，**不是**最新采集器的测试。

```powershell
# 在仓库根目录
python -m unittest discover -s projects/commerce/amazon-jiimore/tests -p "test_*.py" -v
```

新版五组采集器单元测试位于 [`../source/tests/`](../source/tests/README.md)，须按其中的 Python 3.11 与依赖指引运行。单元测试成功不等于 Amazon、Jiimore、SellerSprite 已完成现网登录采集。