# Facebook Capture API 使用说明

完整可运行的 [curl、PowerShell、Python requests、Node fetch 示例](examples/README.md) 覆盖提交、进度、结果、下载和批次；新版本的可选 `result_outcome` 区分有证据的零反应与好友姓名可见性限制，详见示例的结果边界表。已有字段保持兼容，空数组本身不是 empty lists 的判据。

批次帖子汇总 CSV 在既有列之后追加 `result_code`、`result_reason`、`result_label`；原有列名及顺序保留。下游读取建议按列名而非固定总列数处理。

本文描述当前 `facebook_capture_api.py` 已实现的本地 HTTP 合约，以及
`facebook_api_client.py` 的最小用法。它不代表任何一次真实 Facebook 抓取已经成功；
真实结果仍须看 job 的终态、`error`、`completion_scope`、完整性报告和 artifact 内容。

## 启动准备

在已经完成本项目依赖、设备配置和人工登录的机器上启动服务。fresh checkout 先在仓库根目录完成对应平台的初始化；这一步只创建虚拟环境、安装固定依赖并以排他方式生成缺失的本机配置，不覆盖已有账号配置：

Windows PowerShell：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -InstallOnly
```

macOS：

```sh
bash setup.sh --install-only
```

随后确认 `.venv` 中是 `mitmproxy==12.2.3`、`frida==17.17.0`、`frida-tools==14.10.4`、`requests==2.32.5`，并按平台部署文档完成 SDK/AVD、合法 Facebook APK、人工登录、公开 CA 和匹配架构的 Frida server。API 不替用户登录或处理验证码。

推荐使用本地启动器，让它读取 `facebook.local.json` 中的 `port`，不要把避让后的端口重新写死为 8765：

Windows PowerShell：

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe facebook_local.py start --no-browser
```

macOS：

```sh
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python facebook_local.py start --no-browser
```

`facebook_local.py start` 只启动 loopback API 并保持当前终端，不负责启动 AVD 或 v2 UI；Windows 完整链路使用 `START.cmd`，macOS 使用 `START.command`。macOS `START.command` 的 API 终端需要保持打开，Windows 一键入口会把 API/UI 放到后台。

如果必须直接运行 `facebook_capture_api.py`，先从同一份配置读取端口：

Windows PowerShell：

```powershell
$apiPort = & .venv\Scripts\python.exe -c "import facebook_local; print(facebook_local.configuration()['port'])"
& .venv\Scripts\python.exe facebook_capture_api.py --host 127.0.0.1 --port $apiPort
```

macOS：

```sh
API_PORT="$(.venv/bin/python -c 'import facebook_local; print(facebook_local.configuration()["port"])')"
.venv/bin/python facebook_capture_api.py --host 127.0.0.1 --port "$API_PORT"
```

服务只接受 loopback bind；它会拒绝把监听地址改成公网或局域网地址。客户端默认使用
`http://127.0.0.1:8765`，如果 `facebook.local.json` 使用了其他端口，请在示例的 `base`
或 CLI 的 `--base-url` 中替换它。`requests` 已列入项目依赖；独立客户端环境可只安装
`python -m pip install requests==2.32.5`。Windows 完整一键启动也可运行 `START.cmd`，
macOS 可运行 `START.command`；两者都会以本机配置端口为准。
首次下载仍须按 [README](README.md#下载后如何开始) 准备 SDK、专用模拟器、证书、匹配架构的 Frida 和人工登录；UI Helper 是可选项，默认 ADB/XML 路径不依赖它。API 不会替用户完成账号验证。Windows 完整启动也可运行 `START.cmd`，macOS 使用 `START.command`；端口在 `facebook.local.json` 中配置。不要选择模拟器已占用的 `5555` 作为 API 端口。

这个服务是单执行器、串行队列（`health.mode` 为 `primary-standby-serial`），不是多租户
服务。primary/standby 是 worker 故障转移，不是并行租户隔离；不要把它直接暴露给不受信任
的网络。

## 三步 requests 示例

下面是与现有路由和字段一致的三步流程。`reaction_scroll_limit` 被省略时保持服务端的
全量/terminal 默认语义；只有明确传整数才是 bounded sample。

```python
import time
import requests

base = "http://127.0.0.1:8765"
session = requests.Session()

# 1. POST /v1/jobs：只提交一次；202 返回 job_id。
submitted = session.post(
    f"{base}/v1/jobs",
    json={"post_url": "https://www.facebook.com/example/posts/1"},
    timeout=30,
)
submitted.raise_for_status()
job_id = submitted.json()["job_id"]

# 2. GET /v1/jobs/{job_id}：只轮询 GET；不要在超时后自动 POST 重复提交。
deadline = time.monotonic() + 900
while True:
    if time.monotonic() >= deadline:
        raise TimeoutError(f"job {job_id} still running; it was not cancelled")
    status = session.get(f"{base}/v1/jobs/{job_id}", timeout=30)
    status.raise_for_status()
    job = status.json()
    if job["status"] == "completed":
        break
    if job["status"] in {"failed", "cancelled"}:
        raise RuntimeError(f"job {job_id}: {job.get('error') or job['status']}")
    if job["status"] not in {"queued", "running", "cancelling"}:
        raise RuntimeError(f"unknown job status: {job['status']!r}")
    time.sleep(2)

# 3. GET /v1/jobs/{job_id}/result：仅 completed 才读取结构化结果。
result = session.get(f"{base}/v1/jobs/{job_id}/result", timeout=30)
result.raise_for_status()
payload = result.json()
print(payload["completion_scope"], payload["reaction_users"])
```

更稳妥的 Python 调用是：

```python
from facebook_api_client import FacebookApiClient

with FacebookApiClient(base, session=session) as client:
    job = client.submit(post_url="https://www.facebook.com/example/posts/1")
    completed = client.wait(job["job_id"], overall_timeout=900, poll_interval=2)
    result = client.result(completed["job_id"])
```

`FacebookApiClient` 不会自动重试 POST，也不会把 wait 超时转成 cancel。`session` 可以是
已有的认证 Session，`headers` 可以附加受保护转发所需的认证头：

```python
client = FacebookApiClient(
    "https://private-forwarder.example",
    session=session,
    headers={"Authorization": "Bearer <short-lived-token>"},
)
```

不要把真实 token 写入日志、文档或提交记录。

本地 API/UI 会校验 `Host` 为其实际监听端口上的 `127.0.0.1`、`localhost` 或 `::1`；
浏览器提供 `Origin` 时，必须与请求的本机来源一致。普通 Python/curl 客户端无需新增 token。
若使用已认证的私有转发器，应由转发器先校验外部来源和身份，再把上游 `Host` 改为本地 API
地址；浏览器 `Origin` 也只能在完成该检查后改为上游来源。不要直接透传外部域名的 `Host`，
也不要无条件删除来源校验。请求体上限为 64 KiB，不支持 chunked 或重复 Content-Length。

客户端 `timeout` 使用 requests 的 connect/read timeout 语义：单个数字同时作为连接和读取
超时，二元组可分别指定 `(connect, read)`；它不是严格的总墙钟截止。`wait()` 的
`overall_timeout` 是客户端本地轮询截止时间，`0` 表示执行一次即时状态检查后快速超时；
一次正在进行的 HTTP 请求仍可能受其 read timeout 语义影响而稍后返回。`poll_interval` 必须
是有限的正数，避免忙轮询；`overall_timeout` 必须是有限的非负数。

## 状态、错误和 HTTP

当前服务实际产生的单 job 状态是：

| 状态 | 客户端行为 |
| --- | --- |
| `queued` | 继续 GET 轮询 |
| `running` | 继续 GET 轮询 |
| `cancelling` | 已请求取消，仍需继续 GET 轮询，等待进程停止确认 |
| `completed` | `wait()` 返回；随后可 GET `result` |
| `failed` | `wait()` 抛 `FacebookJobError`，包含 `job_id` 和服务 `error` |
| `cancelled` | `wait()` 抛 `FacebookJobError`，包含 `job_id` 和服务 `error`/状态 |

运行中的任务取消后，在停止尚未确认时保持 `cancelling`，确认停止后才成为 `cancelled`。
Windows 任务的状态还会带 `transport_cleanup_state` 和 `transport_cleanup_error`：成功清理为
`restored`，失败时保留明确错误并阻塞 capture lease，直到清理重试成功；不能用 `cancelled: true`
或 HTTP 200 代替清理确认。macOS runner 自己在 finally 阶段清理传输，Windows 的 PowerShell
清理辅助程序由 API 受控调用。
任何其他字符串（包括空缺状态）都会抛
`FacebookUnknownStatusError`，不会无限轮询。

当前单 job 路由的 HTTP 合约：

| 请求 | 成功 | 当前错误 |
| --- | --- | --- |
| `POST /v1/jobs` | `202`，JSON 含 `job_id` | `400`：JSON/字段/大小无效 |
| `GET /v1/jobs/{id}` | `200`，job 状态 | `404`：job 不存在 |
| `GET /v1/jobs/{id}/result` | `200`，仅 completed 可取 | `404`：不存在；`409`：尚未 completed 或服务结果错误 |
| `GET /v1/jobs/{id}/progress` | `200`，实时状态/产物元数据 | `404`：job 不存在 |
| `GET /v1/jobs/{id}/artifacts/{name}?download=1` | `200`，二进制内容 | `404`：job/name 不存在、未选中或尚未可用 |
| `POST /v1/jobs/{id}/cancel` | `200`，含 `cancelled`/`reason`/`job` | `404`：job 不存在 |

客户端还会明确区分：连接/读取 timeout 等 `FacebookApiTransportError`、非 2xx 的
`FacebookApiHttpError`、成功但非 JSON 的 `FacebookApiProtocolError`、总等待时间耗尽的
`FacebookWaitTimeoutError`。这些异常都不会暗中重复提交。HTTP 非 JSON 错误的响应正文只
用于有限长度诊断，不会被当作成功结果。

批次和诊断路由：

| 请求 | 语义 |
| --- | --- |
| `POST /v1/batches` | 提交 `post_urls` 数组，最多 100 个去重 URL，返回 `202` 和 `batch_id`/子 job。 |
| `GET /v1/batches/{id}` 或 `/progress` | 读取批次状态、每个子 job 和汇总进度。 |
| `GET /v1/batches/{id}/artifacts/{name}` | 下载批次汇总 artifact；逻辑名由服务返回的列表决定。 |
| `POST /v1/batches/{id}/cancel` | 对批次子 job 发起取消；仍须逐个读取子 job，直到取消和清理确认。 |
| `GET /health` | 读取 `service`、`mode`、worker 和 `capture_lease`；`service=ready` 只证明 API 可用。 |
| `GET /v1/setup`、`GET /v1/capabilities` | 读取本机 doctor/平台能力；Windows/macOS 的 `capture_pcap_supported` 均为 `true`（实现能力；设备 root/tcpdump 前置仍由 runner 验证）。 |
| `POST /v1/workers/reload`、`POST /v1/workers/{id}/enable`、`POST /v1/workers/{id}/disable` | 维护 worker 配置；必须在没有运行任务且设备已单独验收时使用。 |

Worker 配置写入失败时，当前内存配置和调度选择保持不变。`reload` 与 `disable`
均不允许停用有运行/取消中任务的 active Worker；被拒绝的 reload 不会部分加载新增
Worker 或更换当前执行者。reload 不会代替操作者回退外部修改的磁盘文件：确认任务结束后
再修正配置并重载，勿靠重启绕过进程、cleanup 或 lease 保护。

Worker 管理接口若遇到本地文件 I/O 故障，返回 `503` 和已有格式的 `{"error": ...}`，
不返回本机路径或原始异常内容，也不将该情况误报为后端进程离线。正常成功响应保持 `200`，
未知 Worker 保持 `404`，配置/忙碌状态冲突保持 `409`。503 表示本次操作失败，不是整个采集
服务完成或退出的证明；先检查本机配置文件权限、磁盘和当前状态，不要盲目重复启停或重启。

单任务/批次提交遇到存储 I/O 故障时返回脱敏的 `503`；请求正文读取超时仍返回 `408`，
参数/JSON 错误仍返回 `400`，正常接受仍返回 `202`。不要自动重试 POST：先检查当前任务状态，
因为请求失败或响应断连本身不能证明此前完全没有接受任务。

批次提交先准备输出和子任务 manifest，再保存已有格式的 batch manifest 作为提交标记。
queued 子任务只有匹配同批次、同版本且包含其 job_id 的提交标记，才可在重启后自动恢复。
不完整提交被保留为失败记录而非执行；若同时存在进程/cleanup 不确定证据，lease 保护仍保留。
旧的已提交 version-1 批次继续可恢复；失败产物不会为修复检查而被删除。

## 结果存储故障的显式确认恢复

新增 `POST /v1/jobs/{job_id}/reconcile-result-persistence`，只用于已经保存并登记的
`failed-result-persistence` 失败任务。它确认操作者已检查失败输出和本机存储问题，**不是**
结果正确、完整或成功的证明。原任务保持 `failed`，不会自动重抓、改为 completed 或删除产物；
解除最后一个符合条件的阻塞后，其他正常排队任务可能继续运行。

确认前必须检查任务/health、磁盘及失败产物，确认无活动抓取、停止状态/PID/句柄已核验、
平台传输恢复明确且没有清理错误、无未解决设备故障。未知 PID、未恢复代理、其他存储故障、
全盘不可写且没有保存诊断均不符合条件。不要靠启用 Worker、循环 cancel、重启或删除文件强行解阻。

只有完成上述检查并且决定允许其他队列任务继续时，才手动执行一次：

```powershell
$base = 'http://127.0.0.1:8765' # 实际端口以本机配置为准
$jobId = '<已检查的失败任务ID>'
Invoke-RestMethod -Method Post -Uri "$base/v1/jobs/$jobId/reconcile-result-persistence" `
    -ContentType 'application/json' -Body '{"acknowledge":true}'
```

正文必须恰好只有 `acknowledge:true`，不能是字符串、数字、重复字段或额外参数；共享 Host/Origin
和 64 KiB 长度保护继续生效，GET 不执行确认。服务是本机操作者信任模型，不是多租户鉴权；
外部私有转发器必须先建立身份/来源检查并正确转发上游 Host/Origin。

成功 `200` 返回 `{"reconciled":true,"job":...}`，任务仍失败；`400` 正文非法，`404` 未知任务，
`409` 不符合状态/保护条件（含重复确认），`408` 正文读取超时，`503` 本地 I/O 错误（脱敏）。
**不要自动重试这个 POST。** 响应断连或 I/O 错误不能证明没执行过；先重新检查 job、health、
队列和存储诊断再决定。确认记录保存失败不会按正常路径清除该任务阻塞；其他任务阻塞仍独立保留。
此入口不是断电/磁盘损坏/多进程 exactly-once 或真人设备恢复保证。

若确认记录已保存，但后续后台调度线程无法启动，入口也会返回脱敏 `503`，提示先检查状态。
此时旧任务可能已经是 `failed-result-persistence-reconciled`；`503` **不保证全部操作回滚**，
不能自动重试确认 POST。先读取原任务和队列/health，再按正常操作员流程处理调度问题。
Worker 重载、启用入口遇到同类线程启动错误，也返回脱敏 `503`；既有 `200/404/409` 不变。


## 结果完整性

completed 是服务任务的终态，不等于客户端可以跳过完整性检查。结果 JSON 的
`completion_scope` 有两种当前值：

* `terminal`：服务端通过当前解析/分页完整性条件，允许作终止分页结果声明。
* `bounded-sample`：调用方显式传了 `reaction_scroll_limit`，结果在 cap 处停止；这是有界样本，
  不能描述为全量。

`partial_evidence_available` 出现在 job 状态中，表示磁盘上有安全抓取证据；它不是成功或
全量声明。`failed`/`cancelled` 也可能保留 checkpoint、CSV 或安全 flow，但必须按失败/取消
处理，不能调用 `result()` 伪装成 completed。限流、open-tail、目标不匹配、解析错误等应以
服务返回的 `error`、`completeness`、`transport_observation` 和报告文件为准；本客户端不把
HTTP 200 或空列表自动升级为真实抓取成功。

`terminal` 只证明当前账号可见名单已经终止。页面总数大于实际可见用户数时，
`complete-terminal-visible-subset` 和可见性字段会保留该差异。目标已验证、Reaction 查询已终止、
传输及诊断无错误的 friends-only 可见性结果可能合法返回 `reaction_users: []`；调用方应保留
这些范围说明。`query-not-triggered` 不满足这个条件，仍会失败。

## Artifact 下载

artifact 路由接收逻辑名称，不接收服务端返回的本机路径。常用结果文件是：

* `facebook-sample-patterns.json`
* `facebook-reactor-users.csv`

当前服务的完整单 job artifact 名称还包括：`raw-network.pcap`、
`raw-network-capture.json`、`raw-facebook-http.mitm`、`raw-facebook-http-index.jsonl`、
`raw-facebook-http-summary.json`、`safe-facebook-http-capture.jsonl`、
`facebook-live-progress.json`。其中 `safe_capture`、`progress`、`pcap`、`mitm` 是提交时的
可选保存组名称，不是下载 URL 中的 artifact 名。
逻辑名 `facebook-reactor-users.csv` 在服务端内部才映射到 `analysis/facebook-reactor-users.csv`；
调用方不要拼接 `analysis/`。

Windows 与 macOS 都接受 `pcap`：两者在指定 Android 设备内运行 tcpdump，不使用宿主机 sudo 或 Windows 抓包驱动。设备必须已有可用的 `su 0` 和 `/system/bin/tcpdump`；Mac 会验证本次进程身份、停止状态、PCAP 头和远端/本地 SHA-256，再交付 raw-network.pcap 与 raw-network-capture.json。失败/取消时恢复代理并保留不确定清理保护，不自动重启 ADB。`mitm`、`safe_capture`、`progress` 按请求选择保存。Mac 支持 collect=["shares"] 或 ["reactions","shares"]；组合模式先验证 Reaction，再新鲜打开同一目标的分享名单，只把当前账号可见终页名单作为结果，不宣称所有分享均可见。

```python
client.download(
    job_id,
    "facebook-reactor-users.csv",
    "output/facebook-reactor-users.csv",
)
```

下载先写同目录随机临时文件，校验响应长度后再 `os.replace`。连接中断、长度不符、HTTP
错误或空响应会抛 `FacebookApiDownloadError`，不会留下半个目标文件；若目标已存在，失败时
保留原文件。为避免 requests 自动解压 gzip 后把解压长度误与压缩 `Content-Length` 比较，
当前客户端只接受未编码或 `identity` 响应；收到其他 `Content-Encoding` 会明确拒绝并保留
完整性检查。服务返回的 `run_dir`、`job_log` 等本机路径仅是诊断信息，客户端不会读取它们。

## 取消和重启恢复

显式取消：

```python
reply = client.cancel(job_id)
```

尚未启动的 `queued` 任务可以直接取消；运行中的任务先记录取消请求，再确认采集进程
停止。停止未确认时，`job.status` 保持 `cancelling`，`process_state` 会反映停止确认情况，
采集保护锁继续阻止任务重叠。调用方应以 `job.status == "cancelled"` 判断取消已完成，
不能仅凭取消接口返回 HTTP 200 判断进程已停止。响应顶层的 `cancelled` 保留兼容语义，
表示本次是否新接受了取消请求；重复请求可能返回 `false`，仍应查看 `job.status`。
对已终态任务，接口返回“已经是该状态”的说明。若 `failed` 任务仍有待完成的
transport cleanup，再次调用该取消接口会先验证原采集进程已经停止，然后重试清理；
原来的 `failed` 状态和采集错误仍保留。检查 `transport_cleanup_state`、
`transport_cleanup_error` 和 `/health` 的 `capture_lease` 确认是否恢复可用；
进程身份或清理证据不明确时继续阻塞后续任务。客户端 wait 超时不会调用这个接口，
是否取消或重试清理必须由调用方明确决定。

job manifest 持久化在服务端 `api-jobs/fb_*.job.json`。服务重启时：

* `queued` 任务在没有遗留进程证据时恢复入队；
* `running` 任务在确认 capture lease 已清理时转为 `queued`，并标记
  `interrupted by API service restart; safely requeued`，保留 checkpoint/attempt 记录；
* `cancelling` 任务保留取消意图，不会恢复入队；停止证据明确时转为 `cancelled`，否则继续
  保持 `cancelling` 和采集保护锁，避免旧进程与新任务重叠；
* 旧版本留下的 `cancelled` 记录如果仍带有未确认的进程证据，也按 `cancelling` 恢复，
  不再把“请求过取消”误当作“已确认停止”；
* 如果旧进程仍在运行，或 PID 身份无法验证，服务不会自动接管，任务会变为 `failed`，并阻塞
  worker takeover；
* 已完成、失败、取消任务从 manifest 恢复其可见状态。恢复不是客户端重复 POST。

因此，服务重启后可用同一个 `job_id` 调用 `wait()`/`get_status()`；不要因为客户端进程
退出就重新提交同一个 URL。

## 远程私有转发

不要为了远程访问修改服务的 `--host`。在受控机器上保持 loopback，然后使用 SSH 本地端口
转发或已经认证的私有隧道，例如：

```powershell
ssh -N -L 8765:127.0.0.1:8765 user@capture-host
```

本机客户端仍使用 `http://127.0.0.1:8765`；隧道、认证头、ACL 和审计由转发层负责。若
使用 HTTPS 私有 forwarder，则把 `base_url` 指向该私有地址并通过 `Session`/`headers` 传递
认证，不要把 API 监听到公网。

## CLI

新任务（默认全量语义）：

```powershell
python facebook_api_client.py "https://www.facebook.com/example/posts/1" `
  --base-url http://127.0.0.1:8765 `
  --output-dir .\client-output
```

显式 bounded sample：

```powershell
python facebook_api_client.py "https://www.facebook.com/example/posts/1" `
  --reaction-scroll-limit 20 --output-dir .\client-output
```

恢复已有任务（不再 POST）：

```powershell
python facebook_api_client.py --job-id fb_0123456789abcdef `
  --output-dir .\client-output
```

CLI 在提交后会立即 flush 一行只含 `job_id`/`status` 的 JSON，便于超时或中断后找回任务；
完成后再保存 `result.json` 并下载 `facebook-reactor-users.csv`。它不会把认证 header 打印
出来，也不会声称真实抓取成功；请检查最终输出中的 `completion_scope` 和结果完整性字段。

## 伪代码参数修正

| 不准确的写法 | 当前合约 |
| --- | --- |
| `submit(url=...)` | `submit(post_url=...)`，或 HTTP JSON 的 `post_url` |
| `max_scrolls=20` | `reaction_scroll_limit=20`；省略即不改变全量默认 |
| artifact `analysis/facebook-reactor-users.csv` | artifact 名 `facebook-reactor-users.csv` |
| `wait(timeout=...)` | `wait(overall_timeout=..., poll_interval=...)`；HTTP `timeout` 是单次请求超时 |
| 超时后重新 POST | 保留原 `job_id`，先 `get_status()`；如需停止再明确 `cancel()` |


## 调度器存活诊断

`GET /health` 保留原有字段，并追加 `scheduler`：`running/true` 表示当前观察到线程存活；
`not-started/false` 表示没有已启动线程；`stopped/false` 表示线程有身份但已结束；
`unknown/null` 表示存活探测失败。它是采样诊断，不暴露线程身份或启动参数。

`service=ready` 只说明 HTTP 服务能响应，`worker=idle` 是任务活动状态，不单独保证后台调度器存活。
探测结果不会自动重启、清除任务/线程句柄或释放 lease；也不是任务成功、结果完整或清理完成的证明。
旧客户端可忽略新增字段。出现 503 后先检查任务、队列、lease 和 scheduler，再决定正常操作员恢复步骤，
不要自动重试确认或提交 POST。


### 产物路径与链接兼容性

下载仍只接受已定义的逻辑产物名。任务/批次根目录下面的符号链接，或解析到根目录外的目录链接，
不再作为可下载产物；文件列表也不会将这些文件标为 ready。普通文件和整个可信任务根目录的迁移/链接仍保留。
如果以前通过任务内链接引用外部 CSV，需要将合法产物复制为任务内普通文件，再下载；不要为绕过检查修改 manifest。
该检查不是防恶意本机写入者的文件系统沙箱，不宣称消除硬链接或检查与读取之间的竞争。


### 健康检查的 ADB 采样

同一次 `GET /health` 只共享一次已校验的设备清单；启动状态和代理仍分别探测，原有超时及错误字段保留。
请求结束（含异常）即丢弃清单，不使用跨请求 TTL 缓存；并发请求各有独立采样。
清单失败、格式不完整或设备条目重复时回退逐设备查询，可能多一次有界清单探测。
此优化只减少健康查询中的本机 ADB 子进程，不改变采集、设备故障判定或稳定等待，也不保证 Facebook 抓取提速。
