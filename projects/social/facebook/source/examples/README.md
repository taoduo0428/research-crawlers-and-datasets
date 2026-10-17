# API 可运行示例

先按平台文档启动 API。服务默认 `http://127.0.0.1:8765`，实际以本机配置为准；本地 API **没有 API Key / Cookie / Authorization 要求**，只绑定 loopback。POST 使用 `Content-Type: application/json`。不要使用负责人伪代码中的 `/submit`、`parameters=` 或 `finished`；实际契约见 [API_USAGE](../API_USAGE.md)。

## 提交任务、查进度、下载结果（Python、PowerShell、Bash）

下列脚本都会先 GET `/health`，仅提交一次，轮询 job 与 `/progress`，遇到 `failed/cancelled` 返回非零；只在 `completed` 后读 `/result` 并下载 `facebook-reactor-users.csv?download=1`。等待超时不会取消、重试提交或假装任务失败，保存输出的 job_id 可继续读取。同名输出文件会被替换，请选择独立路径。

```powershell
# Windows / 项目已安装 requests
.venv\Scripts\python.exe examples\capture_requests.py --url 'https://www.facebook.com/share/r/1Dg59X6ysJ/' --output reel.csv
powershell -NoProfile -ExecutionPolicy Bypass -File examples\capture.ps1 -Url 'https://www.facebook.com/share/r/1Dg59X6ysJ/' -OutputPath reel.csv
```

```sh
# macOS；requests 来自项目 requirements；Node 仅运行 fetch 示例时需要。
.venv/bin/python examples/capture_requests.py --url 'https://www.facebook.com/share/r/1Dg59X6ysJ/' --output reel.csv
# 本仓库未收录 capture_fetch.mjs；Node 的批量提交例子在后面
PYTHON=.venv/bin/python bash examples/capture_curl.sh 'https://www.facebook.com/share/r/1Dg59X6ysJ/' reel.csv
```

原工程有 Node 客户端，但 `capture_fetch.mjs` 没收入这份仓库；下面提供 Node 批量提交代码，可自己保存为 .mjs 使用。curl 脚本要求 Bash、curl 和 Python 3（Python 只解析 JSON，无 jq 依赖）；Windows 可在 Git Bash 中设 `PYTHON=.venv/Scripts/python.exe`。Node 示例使用 Node 22+，不需要 npm install。

恢复已有任务：Python/Node 使用 `--job-id fb_xxx` 替代 `--url`；PowerShell 使用 `-JobId fb_xxx` 替代 `-Url`；Bash 使用 `bash examples/capture_curl.sh --job-id fb_xxx output.csv`。

端口变更：Python/Node `--base-url http://127.0.0.1:PORT`，PowerShell `-BaseUrl ...`，Bash 环境变量 `FACEBOOK_API_BASE=...`。等待上限分别为 `--timeout`、`-TimeoutSeconds`、`WAIT_SECONDS`，默认 1800 秒；单次 HTTP 请求上限 30 秒。网络超时后先查询原 job_id，不自动重发 POST。

## 五类 URL

以下为历史真实测试/观察地址，不承诺当前账号仍可访问或数量不变。每次使用任选一个，不必全部重复采集。

| 类别 | 可用于 `--url` / `post_url` 的地址 |
| --- | --- |
| 普通数字帖子 | `https://www.facebook.com/100063690504939/posts/1691321539667513` |
| share/p 短链 | `https://www.facebook.com/share/p/1DVLR4FNo6/` |
| pfbid 帖子 | `https://www.facebook.com/ATCTPE/posts/pfbid02qYWESeXuh3yj4oH6jHoy6xNKJLg8L777N5fFmjbZYNChhJ45ZE9S7CKtqKicdT7rl` |
| 群组 permalink（也支持 posts） | `https://www.facebook.com/groups/132423725423951/permalink/1415834113749566/` |
| Reel / share/r | `https://www.facebook.com/share/r/1Dg59X6ysJ/` 或 `https://www.facebook.com/reel/1054985397317048` |

## 批量提交

每批最多 100 个 URL，重复项去重；执行器依然串行。以下四种提交方式等价，**任选一种**，不要同时运行造成四个批次。未传 `reaction_scroll_limit` 保持终点采集默认；传整数表示有界样本。

```sh
# curl / Bash
curl --noproxy '*' --fail --silent --show-error --max-time 30 \
  -H 'Content-Type: application/json' \
  --data-binary '{"post_urls":["https://www.facebook.com/share/p/1DVLR4FNo6/","https://www.facebook.com/share/r/1Dg59X6ysJ/"],"collect":["reactions"],"save_artifacts":["progress","safe_capture"]}' \
  http://127.0.0.1:8765/v1/batches
```

```powershell
$base = 'http://127.0.0.1:8765'
$body = @{ post_urls = @('https://www.facebook.com/share/p/1DVLR4FNo6/', 'https://www.facebook.com/share/r/1Dg59X6ysJ/'); collect = @('reactions'); save_artifacts = @('progress', 'safe_capture') } | ConvertTo-Json
$batch = Invoke-RestMethod "$base/v1/batches" -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 30
$batch.batch_id
Invoke-RestMethod "$base/v1/batches/$($batch.batch_id)/progress" -TimeoutSec 30
```

```python
import requests
base = 'http://127.0.0.1:8765'
r = requests.post(base + '/v1/batches', json={
    'post_urls': ['https://www.facebook.com/share/p/1DVLR4FNo6/', 'https://www.facebook.com/share/r/1Dg59X6ysJ/'],
    'collect': ['reactions'], 'save_artifacts': ['progress', 'safe_capture'],
}, timeout=30)
r.raise_for_status()
batch = r.json()
print(batch['batch_id'])
r = requests.get(f"{base}/v1/batches/{batch['batch_id']}/progress", timeout=30)
r.raise_for_status()
print(r.json())
```

```javascript
// Save as batch.mjs and run node batch.mjs (Node 22+).
const base = 'http://127.0.0.1:8765';
const response = await fetch(base + '/v1/batches', {
  method: 'POST', headers: {'Content-Type': 'application/json'}, signal: AbortSignal.timeout(30000),
  body: JSON.stringify({post_urls: ['https://www.facebook.com/share/p/1DVLR4FNo6/', 'https://www.facebook.com/share/r/1Dg59X6ysJ/'], collect: ['reactions'], save_artifacts: ['progress', 'safe_capture']})
});
if (!response.ok) throw Error(`HTTP ${response.status}: ${await response.text()}`);
const batch = await response.json();
console.log(batch.batch_id);
const progress = await fetch(`${base}/v1/batches/${batch.batch_id}/progress`, {signal: AbortSignal.timeout(30000)});
if (!progress.ok) throw Error(`HTTP ${progress.status}: ${await progress.text()}`);
console.log(await progress.json());
```

GET `/v1/batches/{batch_id}` 与 `/progress` 返回批次状态、`posts`（每项含 job_id/status）和 `artifacts`。批次结束不等于每条成功，必须检查每个 `posts[].status`；对完成的 job 使用上面脚本的 resume 模式获取结果/单帖 CSV。批次 artifact 只下载 `state=ready` 的返回项，使用返回的相对 `url` 加 `?download=1`，不要把磁盘 `path` 当 HTTP URL。不同帖子同一用户保留各自记录。取消整个批次用 POST `/v1/batches/{batch_id}/cancel`，随后轮询到清理确认。

## 结果与正常边界

`completed` 表示任务达到其定义的完成范围，不保证所有 Facebook 用户身份均公开。下表为字段解释，数量是示意，非最新真实采集报告；完整返回还有更多字段。

| 场景 | 应核对的字段与含义 |
| --- | --- |
| 正常名单 | `/result` 的 `reaction_users` 有数据；继续检查 `completion_scope`、`target_verification`、`completeness` 和 `visibility` |
| 有界样本 | 显式设置 `reaction_scroll_limit`；`completion_scope=bounded-sample`，不能宣称全量 |
| 真实零反应 | 新版本 `result_outcome.code=empty-lists`、`reason=zero-reactions`；需匹配目标、终点及允许完成的报告，CSV 可只有表头 |
| 仅好友姓名可见且无可返回用户 | `result_outcome.code=empty-lists`、`reason=friends-only-names`；UI 显示 `Empty lists（名单不可见）`，不宣称已获取总数对应全部身份 |
| 未触发列表 / 目标不匹配 | `failed` 与 `error`；不能因为用户数组为空把它归为正常 empty lists |
| 帖子本身不可访问 | 权限/内容不可见与“帖子可见但身份列表受限”是两种情况，依照实际失败和诊断，不强行标成功 |

例如受限结果的**节选**：`{"reaction_users":[],"result_outcome":{"code":"empty-lists","reason":"friends-only-names"}}`。`result_outcome` 为可选兼容字段，普通非空结果可能为 null/缺省；尚未重启加载新版的服务也可能没有它。缺少这个字段时不能仅凭 `[]` 推断原因。

常见 HTTP：非法 URL/JSON/参数为 400；任务/文件不存在为 404；结果尚未完成或当前状态不允许为 409。先看响应 `error`。`queued/running/cancelling` 都不是终态。POST `/v1/jobs/{job_id}/cancel` 只发出取消意图，仍须等待进程和代理清理；客户端等待超时不会替你取消。

默认只请求 progress/safe_capture；它们仍可能包含用户数据，下载文件在本机妥善保管。不要把 CSV、Cookie、原始响应或本机配置加入源码发布。
