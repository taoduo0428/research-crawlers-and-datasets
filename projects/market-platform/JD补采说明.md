# JD 补采

独立脚本 `boss_jd_backfill.py` 使用数据中的固定详情 URL 提取岗位 ID，按最初成功请求的形态直接调用职位卡接口补充 JD：无 Cookie、无登录态，`Referer` 使用对应详情 URL。

当前默认模式是 `ip-pool`：8 个 worker 各持有一个 IP 租约，单个 IP 连续使用；成功、404/410 失效链接和无 JD 岗位继续使用当前 IP。`code=35`、其他非零 API code、代理连接失败或异常响应会淘汰当前 IP，当前岗位回队列交给后续 IP。结果逐条写入原检查点，重启会跳过已有成功和允许的失效链接，不重复处理已完成项。

代码副本位于 `C:\Users\lenovo\Desktop\破限\test`；岗位数据、检查点、备份和三份交付文件仍使用原 BOSS 目录：`C:\Users\lenovo\Desktop\外包+岗位爬虫\outputs\招聘--完整数据\BOSS`。

默认参数：

- 传输：直接请求 `job/card.json`
- 代理：IP 号池，每次运行保持 8 个租约
- 并发：8（每个 IP 一个顺序 worker）
- 每个 worker 请求起始间隔：0.3-0.5 秒
- 单个 IP 请求失败后换 IP，不在同一 IP 上重试
- 每个失败岗位最多在当前运行中重试 8 次，未完成项下次启动仍可继续
- HTTP 404/410、岗位无 JD 等链接失效情况记为允许的 terminal 结果
- 不设置全局 code35 熔断，单个 IP 失败只影响当前 worker

Camoufox 列表页上下文模式仅用于诊断：

```powershell
python .\boss_jd_backfill.py crawl --mode camoufox-list-hover --workers 1 --max-inflight 1
```

运行：

```powershell
python .\boss_jd_backfill.py crawl
python .\boss_jd_backfill.py status

需要回到单代理直连诊断时显式指定：

python .\boss_jd_backfill.py crawl --mode direct --workers 1 --max-inflight 1
```

导入已有的有效 JD 结果（可重复执行，按 `record_id` 去重）：

```powershell
python .\boss_jd_backfill.py import --from-jsonl <已有结果.jsonl>
```

抓取完成后回填三份数据：

```powershell
python .\boss_jd_backfill.py apply
python .\boss_jd_backfill.py verify
```

`apply` 会先将三份原文件复制到 `jd_backfill_backups\<时间戳>`，再生成并验证临时文件，最后原子替换。

回滚预演和执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\rollback_boss_jd_backfill.ps1 -WhatIf
powershell -ExecutionPolicy Bypass -File .\rollback_boss_jd_backfill.ps1
```
