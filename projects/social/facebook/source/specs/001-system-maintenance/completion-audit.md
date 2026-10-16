# 当前补充 — Mac API 实际启动接线已修复（2026-10-04）

本轮生产修复提交 `c676363`，后续测试夹具修正提交 `c7ba7d4`；800项本地门禁（3跳过）及双平台CI37171037469通过。API先创建capture.log而runner拒绝非空目录的冲突已通过真实子进程重定向测试复现并修复。只有与继承stdout文件身份一致的单一普通capture.log可以作为内部API例外；其他旧文件、链接及未绑定日志仍拒绝。

此补充替代下文804be91/a4ce4f4为最新源码的描述，但不替代历史证据。下面完整要求级表中的原生Mac、全系统独立审查及广范围性能缺口仍成立，不因这次修复变成全部完成。

---

# 当前交付与验收摘要 — 2026-10-04

**整体维护目标：仍在进行，不是全系统验收通过。** 下文历史快照中“不支持 Mac Shares/PCAP”“hosted CI 未完成”“T080 未复核”等描述已被后续证据取代；不能将其当成当前结论。

## 当前代码交付

- Mac 实际 runner 已接入 Shares 独立/组合采集和 Android 内 PCAP；最新生产提交为 `804be917e4b9b0386e0df9096fd43990ca127f98`，在 `codex/facebook-maintenance-validation` 测试分支。当前 Windows 工作区仍在 `codex/stabilization-20260915`，未提交工作保留；主分支未合并。
- 本地完整门禁 `run-20261004-pcap-lifecycle-01`：796 项，794 通过、2 跳过，退出码 0，原始输出已读回。当前生产提交 Windows/macOS CI `37169519372` 均成功。这是源码/夹具/CI，不是用户 Mac 实机验收。
- 后续审查撤回了一次错误的“独立 Shares 缺陷”判断：原实现本来正确，多余改动已撤回并保留组合 bounded 回归。已确认并修复的另一个问题是 PCAP 提前退出不能凭有效头/哈希报告成功；保留远程证据和清理失败保护。
- 交付核对覆盖 release 白名单189文件。业务代码/测试与远程测试分支匹配；Mac shell 导出时按既有策略转换 LF/100755。五份维护记录此前未同步到测试分支，本轮补齐；更新前 ZIP 为带日期旧快照，不保证文档与最新本地相同。

## 原目标逐项验收边界

| 要求/任务 | 已有证据及当前结论 | 尚未满足的部分 |
|---|---|---|
| FR-001/FR-002/FR-008，T001–T004 | 当前分支/远程提交/白名单/局部diff检查；时间戳备份，保留既有dirty工作区；只推测试分支 | 不宣称可恢复此前所有未记录的机器变动 |
| FR-003/SC-002/SC-004 | 单worker、五URL、source/terminal/visibility、结果/lease故障夹具和门禁；历史Windows真采集另有日期证据 | 最新Mac Shares/PCAP实际导航/终页/root/文件拉取/清理尚未在真Mac验证 |
| FR-004/SC-003，T017 | 当前全部可行本地测试与Windows/macOS hostedCI通过；跳过及原始输出留证 | 无对应机器的实机检查不能改成通过 |
| FR-005/SC-005，T013–T015 | 历史局部health及分析测量只支持方法级改善；不缩等待或完整性检查 | 广范围队列/设备/网络/UI/CPU/内存/I/O/失败率同条件矩阵未完成；没有端到端提速声明 |
| FR-006，T016/T057 | 定向边界审查、release白名单、候选分类及负向夹具已有记录 | 整体独立审查/历史敏感候选独立分类仍缺；未证明全部秘密不存在 |
| FR-007，T012/T024/T073/T081 | 平台源码配对、Mac模拟契约、全部94线索分类及hostedCI；原生ARM64前置明确 | 真实Mac/Android/Facebook登录、CA/Frida/代理恢复和Shares/PCAP验收不可用 |
| T080 | 旧轮限定timing/Reel/scheduler复核已完成 | 不能扩大为新增Mac功能或全系统独立复核 |
| SC-001/T019 | 当前交付一致性与要求级缺口在本表及verification中明确 | 上述未满足项未获得权威证据，不能标整个feature完成 |

权限已给予，不再把缺少操作授权当成唯一阻塞；但是权限不会产生原生Mac或独立审查证据。未勾选任务同时包含多个门禁，不能按数量推断尚有多少未实现功能。没有新缺陷证据时，不为填满checkbox新增功能、重复采集或无目的回归。

## 保护与交付规则

本轮没有操作真实ADB/账号/AVD/证书，没有启动API/UI、重启或终止现有服务，没有删除旧ZIP/日志/备份，也没有发布或合并主分支。使用源码包请解压到新目录，不覆盖本机配置与运行数据。文档同步本身不增加原生验收或独立复核证据。

下面按日期保留历史与阶段记录；若有矛盾，以本摘要及最后对应证据为准。

---

# 当前验收总表（2026-10-04 接续核查）

本次接续目标：继续维护原系统；无原生 Mac 时执行源码配对和无设备模拟契约验证。用户已明确给予继续执行权限，但权限不能替代缺少的机器、账号或实机证据。

## 本轮当前事实

- 工作区仍在 `codex/stabilization-20260915`，保留接续前的全部未提交变更；本轮未切换/合并分支。
- 远程测试分支 `codex/facebook-maintenance-validation` 仍为 `eb737356c1406b107af9474a2962146976c9483e`。
- 本轮通过 GitHub CLI 重新读取 run `37139716569`：Windows/macOS contracts 均 success。北京时间完成时间分别为 2026-10-04 01:16:54 和 01:18:17；这是旧提交的当前可读 CI 记录，不是本轮新增测试的 CI。
- 当前启动前检查：8765/8766/8081 无监听；5037 和 7897 仍有监听。未启动 API/UI、模拟器或执行真实 ADB 查询，不推测服务关闭原因。
- 新增五项实际 Mac runner 契约检查；五类 URL 作为子案例走 shared API -> argv -> active runner parser，不以旧 transport-only 模拟器代替。
- 首轮新增测试有两项因测试夹具漏传必填 output-dir 出错；只修测试夹具，未改变生产命令构造规则。随后聚焦 65 项运行、64 项通过、1 项原生 fcntl 竞争测试跳过；退出码 0。

## Windows / Mac 配对与能力矩阵

| 环节 | Windows 路径 | Mac 路径与本轮结论 | 未取得的证据 |
|---|---|---|---|
| 启动 | START.cmd/PowerShell | START.command -> setup.sh -> facebook_local.py；入口 Git mode 100755，Darwin guard 明确 | 接收方真实启动、安全提示、端口与 GUI |
| 依赖/配置 | Windows venv、SDK 工具与本机配置 | .venv/bin/python；Mac SDK/JBR 路径；本轮 setup/device fixtures 覆盖 | 接收方实际版本、文件、权限与网络 |
| Android 架构 | 历史基线 x86_64 | 新配置链为 Apple Silicon ARM64；Frida-server 必须为 Android ELF，不是 Mac Mach-O | 实际 ARM64 APK/镜像/Frida、设备引导 |
| Reaction | run_facebook_capture.ps1 | facebook_android_capture.py；五 URL 命令绑定、serial/ports/output、默认 unlimited 及显式 cap 合约通过 | 五类 URL 实际导航、抓包、终点与账号 visibility |
| Shares | Windows runner 已有历史可见范围证据 | 当前 Mac build_capture_command 明确拒绝；不是配置问题，不能宣称功能相同 | 若需 Mac Shares，需要另一个明确功能增量和实机验收 |
| PCAP | Windows 路径支持选取原始网络产物 | Mac preflight 明确拒绝 pcap，测试证明在 native tools/ADB 前停止；支持 mitm/safe_capture/progress | Mac PCAP 未实现，不做隐式替代 |
| 传输/取消 | Windows owned-process/transport cleanup | POSIX session/process-group adapter + runner finally/status；失败 cleanup 留保护；临时夹具通过 | 真 Mac SIGTERM、ADB reverse/代理恢复与 lease |
| 目标/完整性 | shared analyzer/progress/integrity | Mac validate_result 保留 target/source、CSV identity、terminal、rate-limit 验证；bounded-sample 不当全量 | 真实采集结果与原生 I/O 故障 |
| 旧模拟入口 | 不属于实际捕获入口 | macos/facebook_macos.py simulate executed=false，仅命令计划；deep_capture_runner not-implemented 是该旧适配器的描述 | 不能据此判断新 runner 不存在或全链路可用 |

无原生 Mac 时，上述模拟能降低已覆盖代码路径的风险，不能替代虚拟化、登录、CA/Frida、GUI、网络与全量终点验收。基础环境已配置属于用户提供的前提，本轮未远程核验该 Mac。

本轮回滚备份：`C:/Users/lenovo/AppData/Local/Temp/facebook-mac-parity-20261004-090312/`。仅测试/维护文档变更；不变更生产 runner、算法、等待、配置、登录态、SDK/Frida/证书版本。

下列为旧快照，若与本轮记录冲突以本轮为准；不得将旧表中“CI/限定复核未完成”等描述继续视作最新结论。

---

# 当前验收总表（2026-10-03）

Windows 本机已有可运行交付，但整体目标尚未全部完成。下面区分代码实现、运行验证和未取得的证据；历史记录保留在后面，不应把早期统计当作最新结果。

| 范围 | 已有证据 | 边界与剩余工作 |
|---|---|---|
| 当前代码回归 | batchmeta-final-01：765 项运行，763 项通过、2 项跳过；退出码 0；原始输出保留 | Windows/Python 3.13.12，不等同远程 CI/Python 3.12 或 Mac 实机 |
| Reaction 真实样本 | 数字帖、share/p、pfbid 各950人；群组308人；Reel2人；均有目标绑定及终页证据 | 只覆盖这些样本；不是所有URL或所有账号可见范围证明 |
| Shares | fb_ad138ecdb11c4fd1：16个当前账号可见分享者，2页终止；报告46、差额30保留 | 不代表取得全部46；其余 Reaction-only任务没有请求 Shares |
| 取消恢复 | fb_4a1224c9bd984084：取消、已登记进程退出、代理恢复、lease释放；结果接口409 | 不宣称所有物理故障、未知子孙进程或断电耐久性 |
| 可靠性修复 | 持久化后再发布、阻塞原因保留、显式确认、启动失败回滚、脱敏503、scheduler存活诊断 | 独立执行者复核未完成；隔离故障证据与实机证据分别保留 |
| 文件交付 | job/batch下载与元数据共用物理路径约束；已有950行CSV经API/UI下载哈希一致 | 不宣称硬链接或恶意本机写入者TOCTOU防护 |
| 已测量优化 | 同主机6组交替对照，health方法475.905→325.145ms，ADB调用5→3，返回内容一致 | 约31.7%仅适用于该健康方法；不是Facebook采集提速 |
| 资源基线 | fb_80a78d0601db4a8a：112.593秒、40次采样；CPU/工作集/I/O已记录 | 单次采样、短命进程可能遗漏、共享页重复计数；不是失败率/全系统资源基准 |
| 平台与发布边界 | 94条平台线索已源码分类；白名单/本地可达历史签名候选已由主执行者分类 | 独立安全分类、原生Mac及hostedCI未完成；没有批准发布 |

最新批次元数据修复已在2026-10-03 22:16通过仅重载空闲API应用；后续读回原Reaction950、Share16、取消结果409及CSV哈希检查正常。该状态是当时快照，不是持续运行保证。日志/配置备份见 verification.md。

## 查收与后续边界

- 使用最新本地源码快照时先解压到新目录，不覆盖当前本机配置或运行数据。每个ZIP内含SHA256SUMS；旧包保留为旧快照。
- 主执行者回归与二次检查已做；T016/T057/T080所需独立复核仍未取得，不能改名冒充。
- T012/T017/T073/T081中需要原生Mac/远程CI的部分明确未验收。
- T013–T015宽范围性能要求仍有缺口。现有结果支持health局部改善及资源基线，不支持采集端到端提速。
- 不再为了未勾选数量而新增功能或重复测试；需新的具体缺陷或明确未覆盖的验收条件才继续变更。T019不得据此标为完成。

## Preserved historical audit (superseded where contradicted by current evidence)

# Completion audit — original system-wide objective remains active

Audit date: 2026-10-02. Scope is the user's full objective: use the engineering
framework to analyze, repair and safely optimize the existing Facebook system.
This document is not a replacement/narrowing of that objective or a success claim.

## Requirements and evidence

| Item | Authoritative evidence available | Status / missing proof |
|---|---|---|
| Understand current system (FR-001) | AGENTS/Constitution/workflow, current dirty HEAD/diffs, module traces, tests/CI, current listener/device queries | Local baseline established; whole-module coverage review incomplete |
| Preserve dirty work/state (FR-002/FR-008) | Timestamped per-slice backups/diffs/hashes, unchanged operator registry timestamps, no reset/commit/push/cleanup command | Preserved in performed slices; not authorization for future cleanup/deployment |
| API and URL compatibility (FR-003) | Dummy API/proxy, five-family validation and client fixtures | Local contracts tested; actual private tunnel and real five-URL runtime acceptance missing |
| Serial Worker, takeover, lease and cleanup (FR-003) | State-machine traces and multiple negative/positive temporary-manifest fixtures | Several defects repaired; operator recovery, all crash points and actual proxy/device restoration missing |
| Target/source/completeness/terminal/visibility (FR-003/SC-004) | Existing deterministic parser/result/recovery tests and selected current-source traces | No real fresh Facebook enumeration; independent whole-contract audit incomplete |
| Regression/evidence quality (FR-004/SC-003) | Per-slice red/green tests, local gate exact exits and summaries, explicit partial/skip semantics | Current optional external full-stream logging verified on Windows; historical tails not recoverable, skip/platform/whole-regression acceptance remains scoped |
| Security boundary review (FR-006) | Local HTTP/URL/DOM/data-path repairs, no production probes; narrowly independent export review | Limited release/local-reachable-history signature snapshot triaged by parent; independent classifications and broad security/leakage coverage incomplete |
| Platform pairing (FR-007) | Windows local tests, static paired code checks, heuristic scan, both CI command lists | 94 heuristic candidates not fully classified; hosted CI, native Mac and Android runtime missing |
| Performance (FR-005/SC-005) | Alternated six-run same-fixture Python benchmarks, exact export equality, allocation tradeoff | Local schema/aggregation benefit only; disk, queue, UI, device, network and E2E baseline missing |
| Safe simple operation (user objective) | Local workflow/gate with failure/partial results and operator docs | Storage-block recovery workflow and real one-click normal/failure acceptance missing |
| Whole completion (T019) | Current-task evidence explicitly partial | NOT MET; do not update goal to complete |

## Current external state

Latest read-only inspection: API 8765/UI 8766 are not listening. Existing ADB
127.0.0.1:5037 and proxy 7897 retain ownership; adb devices -l lists no device.
No Mac execution host is evidenced. Those are current missing acceptance resources,
not permission to reinstall/reset emulators, change accounts or start another tenant.
Code review/fixture work can still make progress, so the whole goal is not at an impasse.

## Task-record interpretation

Early checked T001–T008 and subsequent slice tasks document work performed in their
respective scope; they do not prove every clause of the original objective. In particular,
T003 requires full raw-output retention (not just successful process exit), T004 includes
leakage inspection (release path hygiene is not content/history scanning), and T016/T017
need independent/hosted/native evidence beyond the local fixture suite. Preserve their
history; append explicit outstanding tasks rather than erase gaps or silently redefine
acceptance. Current T009–T019 and independent recovery/platform/E2E tasks stay open.

## Next gates

1. Continue using verified optional external full-stream evidence; bounded summaries
   should label truncation and must not stand in for full failure investigation.
2. Complete independent Worker/persistence/lease review, operator-block recovery and
   restart durability with bounded temporary fixtures, preserving live state.
3. Classify remaining platform/secret/data-contract findings using exact current code.
4. Establish an authorized real-device/host acceptance environment without state resets;
   verify normal, failure/cancel/cleanup, five URL families and artifacts end-to-end.
5. Measure real same-condition performance including correctness and resource costs;
   retain local measurements as local rather than extrapolate percentages.
6. Re-run requirement-level completion audit before any whole-goal success claim.



## Continuation audit — 2026-10-02

The current continuation closed the reproduced sibling registered-handle lease-scan gap and recorded fresh local evidence. The new matrix and full local gate are authoritative for this narrow change only. The original system-wide objective remains **NOT MET**: API/UI are not running, no ADB device is attached, native macOS/Facebook acceptance is unavailable, performance E2E baselines are absent, and remaining durability/leakage/independent review tasks are open. The local gate's 743-test result must not be promoted to whole-system completion.

## Open durability boundary identified after continuation

The existing regression intentionally excludes restart assertions for the `all-postcapture-writes` case because the failure model assumes every post-capture write is unavailable. The current source proves in-memory fail-closed behavior, but it does not yet prove what a second Manager reconstructs from the last durable pre-capture manifest in that specific boundary. T071 is therefore open and must be resolved with an isolated temporary-manifest experiment before any durability or exactly-once claim.

## T071 result

The `all-postcapture-writes` restart boundary is now covered. When no post-capture diagnostic can be durably written, the second Manager reconstructs a failed job with `restart-process-identity-unverified-no-auto-resume`, keeps the capture lease uncertain, has no active worker, and rejects fresh assignment. The absence of a persistence-reason registry in this branch is expected because no diagnostic write succeeded; fail-closed lease state, not an invented durable acknowledgment, prevents recapture. Physical disk/power-loss semantics remain outside this fixture and are still unverified.

T071 is locally closed by the isolated second-Manager experiment and fresh 743-test gate. The result is a fail-closed reconstruction, not a durability guarantee for actual disk/power loss: no persistence-reason registry entry exists when every post-capture write fails, but the reconstructed failed state remains lease-uncertain and unassignable. Native filesystem crash consistency, real devices, macOS and Facebook E2E remain unverified.

## Current runtime reliability focus

The latest bounded runtime fixture suite passed 172 tests across cancellation transport, Worker bootstrap, capture orchestration and shutdown safety. This strengthens local T010/T011 evidence but does not close the real-platform portion: no API/UI service or ADB device is active, and native process, proxy, emulator and Facebook acceptance remain unavailable.

## macOS pairing evidence update

The latest Windows-hosted macOS pairing fixture run passed 16 tests. It improves static/fixture evidence for T012 but leaves native macOS execution and real device acceptance unavailable; no task status is promoted beyond the evidence actually collected.

## Setup/local evidence update

The latest setup/local/readiness/transport fixture run passed 56 tests with one explicit skip. This strengthens deterministic deployment-contract evidence only; it does not establish current configuration ownership, account login, device readiness or live transport health.

## Runtime ownership refresh

The latest read-only snapshot confirms no API/UI listener and no attached ADB device; the existing ADB daemon and verge-mihomo proxy remain owned by their observed processes. This preserves the boundary for local testing: no restart, cleanup or device reset was authorized or performed.

## Final local gate refresh

The current worktree's fresh full local gate passed with 743 tests and two explicit skips. This confirms no regression from the acceptance-matrix/audit-document additions; it does not close the external Windows runtime, Android/Facebook, macOS, hosted-CI, crash-consistency or performance gates.

## T073 real startup update

T073 has partially advanced: the authorized `START.cmd` path now has current evidence for real AVD boot, ADB readiness, proxy/reverse transport, API/UI health and active Worker lease availability. It is not complete: no target URL was submitted, no credentials were entered, and Facebook result/visibility/completeness acceptance remains open.

## Real Facebook session readiness update

The emulator now exposes a usable Facebook feed/session after the authorized startup. Target-specific source/visibility/completeness and terminal-pagination acceptance are still open because no user-approved target URL has been submitted.


## Real numeric-post acceptance — 2026-10-03

Historical target located in FACEBOOK_BROWSER_SAMPLES.md (2026-09-08 sample), reused under current user authorization to continue with previous work-log targets. One POST only, default terminal semantics, no cap/wait/version/config change. Job fb_3a266090484a4e00 targeted numeric post 1691321539667513. Start 2026-10-03T03:32:34.418439Z; finish 03:35:09.557538Z (155.139s). Live result: completed/terminal, target matched by graphql-response-post-id, 39 successful pages, 975 raw reaction rows, 950 unique identities, 25 duplicates, reported total 950, gap 0, terminal page true, open tail false, rate-limit records 0. All group terminal; a separate filtered-reaction group remains open, so no claim that every filtered group was independently exhausted. Shares: query-not-triggered/0 users, NOT accepted as empty or complete.

After completion: process stopped, transport runner-owned, health worker idle/queue0/lease available; Android proxy 127.0.0.1:7897, reverse tcp7897 and tcp8081 present. UIAutomator during capture showed Facebook package controls (no account labels retained). Screenshot visual inspection was unavailable in this model; no screenshot-based acceptance is claimed. Run artifacts: C:/Users/lenovo/Desktop/facebook/runs/api-fb_3a266090484a4e00/attempt-001. This establishes one numeric-post Reaction run, not five URL families, Shares, native Mac, cancellation or same-condition before/after performance.


## Final local handoff gate — 2026-10-03

Original final-handoff131300 process/session vanished; reportempty,stderrunfinisheddots, no completion verdict. Original evidence retained and NOTPASS. One read-only fixture/static retry with a NEWexternal directory completed:749tests128.195s,OK(skipped2),exit0,statuspassed; savedreport and original commandstreams readback+SHA256 verified, integritymanifest at C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261003-final-handoff-retry-01/stream-integrity-final.json. No repeatcapture/APIrestart/independentreview/nativeexecution claim. This includes the new concurrentack regression that predates this gate; previous748 result now historical.

External-statechange before gate: API8765/UI8766 no longer listening, emulator notrunning; oldUIpid44816 reused for cmd.exe created13:12:17, neverkill by stalepid. Adbdevices query automatically started daemon5037 (not a side-effect-free command), no attacheddevice; proxy7897 remains observed. No reason for service disappearance established; no automaticrepair/relaunch/cleanup performed. Earlier successful jobs/cancel prove those dated runs, NOTcurrentruntimehealth.

Residual original objective unmet: independentlyattributed T080 reviewer requires explicit delegationauthorization;T081 nativeMac/hostedCI unavailable (all94sourceleads triaged but not nativeaccepted). No new E2Espeedup claim: T082finitecomparison only correctness/negativeguards, live responsevaried. Broad earlier independent/history/platform requirements remain unclosed; goal NOTcomplete. Further unchanged-loop retests/document append are not meaningful progress. Preserve all dirtysource/rawartifacts/services/userconfig.


## Ledger distinction — 2026-10-03

Independentreview/nativeproof are acceptance obligations, NOT gates against implementing Windows code. Four stale checkboxesT018/T053/T060/T067 corrected from direct evidence; no invented new code requirement or speedupclaim. Stillopen tasks include broad aggregates and independent review/native/performance clauses; their count is NOT unimplemented feature count. Main-agent reread cannot replace an independentreview, but missing reviewer authorization alone is not reason to stop other authorized implementation. Current environment restoration already separately authorized and verified. No commit/push/deletion/nativeMac claim.


## Confirmed unstarted scheduler rollback repair — 2026-10-03

StartWorker helper registered thread/startedIDs before Thread.start; RuntimeError/OSError on demonstrablyunstarted thread left phantom _worker, making later start return an unstartedhandle. Two redsubcases reproduced exactpointerdefect. Minimal repair serializes publication+start underexistingRLock and rollsback only exactownedthread with identNone; restores priorstartedIDs/removesthatregistration, rethrows originalexception. If identnonNone, handle retained and latercalls do NOT startanotherthread. No arbitrary deadworker autorestart, queue/concurrency/timing/visibility/config/API contract change. Native testThread target entersManagerlock successfully (boundedjoin), confirming no start/Managerlock deadlock. No live resourcefault injection.

Source/test/task backups C:/Users/lenovo/AppData/Local/Temp/facebook-worker-start-20261003-135210/. Initial focus91tests hit independentWindows10053 in HTTPsourcefixture: MockManager noStartWorker call, proxylogs403 before unreadbody close; originalfailure retained. Only Host/Origin source-fixtures changed to header-onlyContentLength20 (guard mustrejectBEFOREbodyread), no productionHTTP weakening/auto retry; finalfocus91pass7.992s. Native addedtest included in finalfull752tests129.518s,OK(skipped2),exit0;reports/originalstreams C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261003-worker-start-final-01/. This supersedes749fullbaseline for currentcode. Mainagent evidence, not independentlyreviewed.

Appliedcode via ownedIDLE API reload afterverifyinghealthidle/queue0/cancelling0/leaseavailable and Python listenerabsoluteprojectcmdline+creationtime. Only ownedAPIpid35836 stopped throughprocesshandle; no stalePIDkill/globalSTOP/devicefault/reset. API/UIlogs+configs copied BEFORE existingSTART.cmd to C:/Users/lenovo/AppData/Local/Temp/facebook-api-update-logs-20261003-140231/. STARTexit0,reuseddevice and healthyUI,creatednewAPI;posthealthready/idle/available/queue0,Androiddeviceboot/proxy127.0.0.1:7897,UI200. Configbytehashunchanged. Existingnumeric950terminal/Shares16terminal readable;cancelledresultHTTP409. No repeatFacebookcapture. API rarefailure rollback confirmed in isolatedfixture, not realresourceexhaustion; actualstartup validated nonfailurepath. T084complete within declaredscope;Mac/independentreview/actualE2Espeedup remainunproven.


## Typed scheduler error HTTP completion — 2026-10-03

Three red HTTPoperator cases directly called actual_start_worker with mocked nativeRuntimeError;exceptionescapedreload/enable/reconcile routes. Narrow SchedulerStartError(RuntimeError) now wraps ONLY native Thread.start RuntimeError with original__cause__;OSError unchanged;genericManagerRuntimeError is NOT caught. Three Managerinvoke boundaries maptypederror tosingle sanitized503/inspect-state-before-retry. Responsewriting remainsoutsideManagercatch; even SchedulerStartError in reply propagates, oneManagercall/onewriteattempt,noautoretry. Existing200/404/409/requestguard/statusbody contracts retained.

ActualtemporaryManager direct+v2proxy fixture demonstrates ackmanifest alreadydurablethenThreadfail=>503while jobremainsfailed/reconciled,registrationrolledback,noactualcapture;secondmanualnegativeack rejects409/startonlyonce. Thus503NOTfullrollback;API_USAGE documents inspectjob/queue/health first,noautomaticPOSTretry. Causeidentity/unrelatedlogic/replyerrors assertions covered. Focus71pass15.137s;finalfull755tests136.770s,OK(skipped2),exit0/originalstreams C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261003-scheduler-http-final-01/. Source/test/doc/taskbackup C:/Users/lenovo/AppData/Local/Temp/facebook-scheduler-http-20261003-140854/,boundedproductiondiff48lines. Mainagentreview,notindependent.

Applied by ownIDLE APIreload afterhealthidle/queue0/cancelling0/available pluslistenerPython/projectcmdline/creationtime verified. OnlyAPIpid40600 stopped throughprocesshandle,logs/configsbeforebackup C:/Users/lenovo/AppData/Local/Temp/facebook-scheduler-http-reload-20261003-141911/. STARTexit0/reusedexistingemulator+UI/newAPIready. Posthealthidle/available/device/proxy127.0.0.1:7897/UI200;existingReaction950/Share16preserved;cancelledresult409. ConfigbyteSHA256unchanged. No realthreadresourcefault/storagefault/duplicatecapture/accountreset/versionchanges. T085complete scope;nativeMac/independentreview/E2Espeedup remainnotclaimed.


## Current Windows handoff distinction — 2026-10-03

Implementation applied to current API after owned-idle reload: candidatepersist/lease guards,explicit Reelmapping,passive phase timing,unstarted scheduler rollback,typed startup HTTP503 with cause/inspect-before-retry. Currentfullgate755run/753pass/2skip;successful five-family Reactions,visible Share16/46,real cancel/recovery and preserved results remain scoped evidence. T010/T011 isolation gates closed; remainingcheckboxes mix independentreview/native/platform/performance aggregates, not missingfeaturecount. No independentlyconfirmedall-systempass,Macnative support or E2Espeedup claimed.


## Additive scheduler health snapshot — 2026-10-03

Source health previously reportedready/idle basedonlyHTTP/jobactivity, evenabsent/dead scheduler. Added ONLY scheduler={state,alive} underManagerlock, statesrunning/true,not-started/false,stopped/false,unknown/null onRuntimeError/OSErrorprobe. No nativeIDs/argv/exceptiondetail; preserveoldservice/worker/mode/lease fields. Observerneverdropshandle,clearsjobs/lease,startsworkers or retriesPOST. Sampling is not guaranteed continuousliveness/jobcompletion/cleanup proof. ABI-independent stdthread observation, nativeMac notexecuted. API_USAGE/spec/plan recordadditivecontract.

Two redtests initially6missingfieldsubcases; fixedmissing/unstarted/running/stopped and twoprobeexceptions; ownership/uncertainlease/oldfields preserved. Focus73tests14.588s pass;full757tests130.313s,OK(skipped2),exit0/originalstreams C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261003-scheduler-health-final-01/. Source/test/spec/plan/task/docbackup C:/Users/lenovo/AppData/Local/Temp/facebook-scheduler-health-20261003-181137/. Current code now755pass/2skip, not old755runtotal. No claimed speedup or independentreview.

APIconnection failure was followedbyconfigport8765/listener+process ownershipchecks;confirmednoAPI/UI/captureprocess/marker/capture8081listener. Existingproxy7897/ADB5037 retained. Restoredclosed environment viauser's explicitreopenauthorization, BEFOREstartup4logs+configsbackup C:/Users/lenovo/AppData/Local/Temp/facebook-scheduler-health-restore-20261003-182013/. STARTexit0,newAVDboot/newAPI/UIready;oldstaleAVDlocks movedbyexistinghelper to avd-lock-backup-20261003-182019,no wipe/userdata reset. PostactualGET /health schedulerunning/alivetrue,service ready/workeridle/queue0/cancelling0/leaseavailable,Androiddevice/proxy127.0.0.1:7897,UI200;retainedReaction950/Share16/cancelresult409. ConfigSHA256unchanged. No liveprobeexception/resourcefault injection,repeatcapture/agent/globalwatchdog/versionchange. T086complete currentdeclaredscope.


## Artifact physical path robustness completion — 2026-10-03

Temporary ownfixture junction to sibling syntheticoutside directory reproduced oldartifact() returningallowedCSV beyonddeclaredrunroot (red). Minimal shared_checked_artifact_path resolvestrustedroot/candidate, rejects childsymboliclinks and escapingjunctions before file stat/read, preserveslogicalpath/name andtrustedrootrelocation. Job/batchdownloads and metadata use same check; blockedfiles0bytes/missingnotready. Existing availabilitylabels retained. Helper handlesresolutionOSError/RuntimeError withoutrawpath text. This is accidental/stale-link robustness,NOTconfirmedremote/lower-trustexploit,tenantpolicy or malicioushostwriter sandbox. Hardlinks/TOCTOU/hostilelocalmanifest authority expresslyunproven. API_USAGE/spec/plan detail intentionalchild-link incompatibility andcopyregularartifact guidance;validtrustedrootmove remains allowed.

NativeWindowsjunctionfixture onlyinside explicitlycheckedTEMPparent (target/linkparentcanonicalinside scratch); no actualjoblinks/userfilesaltered. Three tests cover job/batch/metaescape,regularfiles+rootrelocation,missing/empty/resolutionerrors. Initialred case exposedAPIreturn; fixture metadata assertion corrected to existingstate/size schema, no addedavailableflag. Focus9tests4.103s pass;full760tests126.323s,OK(skipped2),exit0/originalstreams C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261003-artifact-confinement-final-01/. Code/test/spec/plan/task/docbackup C:/Users/lenovo/AppData/Local/Temp/facebook-artifact-confinement-20261003-184322/.

Applied afteridentity+idle/queue0/cancelling0/available checks:onlyownedAPIpid8148 stoppedthroughprocesshandle,logs/configsbeforebackup C:/Users/lenovo/AppData/Local/Temp/facebook-artifact-confinement-reload-20261003-185547/. ExistingSTARTexit0/reusedemulator+UI/newAPI;actualAPI8765/v2proxy8766 numericCSVboth200/178253byte/hash=ondisk. Posthealthready/schedulerrunning/workeridle/queue0/leaseavailable/configbytehashunchanged. No repeatcapture or livepathfaultinjection. T087declaredscopecomplete;T009source/localreviewclosed using logicalalias8case proof,physicalfixture,manifestrecoveryT060+T010/T011 andparent release/localhistoryT053 snapshot;NOTindependentT057/securityapproval/allOSfilesystem guarantee. Mainagentreview only, nativeMac/hostedCI/independentreview remainseparate.


## Request-local health ADB batching and measured benefit — 2026-10-03

Initial realHTTP6samples median445.709ms suggested health subprocess overhead; source3workers eachstatequery plus activeboot/proxy=>5ADbcalls. Added lazy ContextVar holder onlywithin health() worker-snapshot list, tokenresetfinally includingexceptions; realadb_snapshot usesonevalidatedunique device-list, boot/proxy queries/timeouts/errorsemantics unchanged. Directsnapshot outsidehealth independent. Injected/mocked snapshotprovider bypasseslazyinventory withoutrealdeviceprobes. Malformed/duplicate/unknownstate/header/exit/OSError/TimeoutExpiredinventory=>oldindividualstates, oneboundedextraquery cost onfallback (no inventedhealthystate). No TTL/persistentcache/paralleltenants/SDKversion/waitchange. Concurrentrequests ownscope, snapshots not continuousfreshness guarantee.

Tests initially3redbatchexpectations(noinventory);5newmethods cover fewercalls/freshnextrequest/directoutsideprobe,invalidlist+proxytimeout preservingdevice,failedinventory3cases,2concurrentrequests/barrierseparateinventories,exceptioncontextcleanup/noinjectedproviderprobe. Focus74tests3.852s plus5finalbatchtests pass;full765tests131.889s,OK(skipped2),exit0/originalstreams C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261003-health-adb-batch-final-01/. Source/test/spec/plan/task/APIUsagebackup C:/Users/lenovo/AppData/Local/Temp/facebook-health-adb-batch-20261003-191920/. No independentlyattributedreview.

Samehost/config/SDKadb/binary/device,6alternatingold/current Managerhealth pairs readonly actualADB, fullreports equal EVERYpair: oldmedian475.905ms/5commands vsnew325.145ms/3commands (~31.7percent wall reduction on this measured method). Originaltoken-freeprivatebenchmarkrecords/hashes C:/Users/lenovo/AppData/Local/Temp/facebook-health-batch-paired-20261003-192350.json. Runtime/jobs empty in controlledManager so no capture/queue interaction; no CPU/RSS/totalprocessmemory metric or FacebookE2Espeedup claim.

Applied viaownidleAPIreload onlyPID46768 verifiedPython/projectpath/creationtime+queue0/cancelling0/available;logs/config backup C:/Users/lenovo/AppData/Local/Temp/facebook-health-adb-batch-reload-20261003-193739/. STARTexit0/reusedemulator/UI;actualHTTPpostapply6samples median312.0365ms, source C:/Users/lenovo/AppData/Local/Temp/facebook-health-batch-live-20261003-193830.json (observational notalternatingHTTPcontrol). Posthealthready/schedulerrunning/idle/queue0/leaseavailable;existingCSVproxyhashunchanged/cancelresult409/configbytehashunchanged. No duplicatecapture/livefault injection/accountreset/globalwatchdog. T088 complete endpointscope;T013-T015 broadercapture/resourcebeforeafter/native/independentacceptance remainseparate.


## Windows 单次真实资源基线（2026-10-03）

任务 `fb_80a78d0601db4a8a`：历史 Reel 样本，单次提交，无抽样上限或等待改动。采集耗时 112.593 秒；采样窗口约 113.84 秒、40 个快照。结果 2 人/报告总数 2、目标匹配、terminal、差额 0，进程 stopped、传输 runner-owned；观察脚本退出码 0。

| 观察范围 | 观察到的 CPU 秒 | 采样工作集峰值之和 MiB | I/O 读取传输 MiB | I/O 写入传输 MiB |
|---|---:|---:|---:|---:|
| api | 7.000 | 58.91 | 57.69 | 40.15 |
| capture-tree | 25.641 | 499.91 | 74.35 | 40.00 |
| emulator | 191.000 | 1817.88 | 204.73 | 40.38 |

限制：采样后等待 2 秒，加上查询时间，实际间隔并非严格 2 秒。短命进程和两次采样之间的峰值可能漏采；观察到 27 个捕获树进程，不是所有曾启动进程的全量证明。API/模拟器用窗口计数差值；新捕获树进程用最后观察到的累计值。工作集相加含共享页，不是系统物理内存总量；I/O transfer 包含文件、管道、网络，不能叫物理磁盘吞吐。模拟器指标含 Android 后台活动；API指标含观察脚本轮询和用户页面请求。多核 CPU 时间可超过墙钟时间。

这是一份单次资源基线，不是优化前后对照或失败率统计，也不足以完整关闭 T013–T015。不能据此减少模拟器 CPU/RAM、缩短稳定等待或宣称采集提速。生产代码和既有配置未改；未安装 psutil。未重复提交、中断/杀死采集、重启环境或做压力测试。

证据目录：`C:/Users/lenovo/AppData/Local/Temp/facebook-live-resource-20261003-212126/`，包含 sample.ps1、ownership.json、samples.jsonl、summary.json、observer.log；报告不保存用户姓名、账号凭证或命令行。当前 API ready、scheduler running、Worker idle、队列 0、lease available。


## Current branch/review update — 2026-10-03

T080 is closed: independent bounded review plus current test-branch Windows/macOS CI passed. The review found and the branch fixed a scheduler reload retry P2. T081 remains partial because native Mac/hosted user environment and functional counterpart acceptance are not established. T019 remains NOT MET; no merge/release/native Mac/E2E speedup claim.


## Mac pairing local gate readback — 2026-10-04

Full current local gate passed/ok=true/exit0: 776 tests in 126.188s, 774 passed and 2 skipped. Eight started command results all exit0 with retained original stdout/stderr files verified; release-path gate189 files/noerrors. Report and full streams at C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261004-mac-parity-01/. Summary unittest tail was truncated, so actual original stderr readback confirmed count/OK. This supersedes earlier local test counts for this checkout only; old hostedCI covers eb737356, not these five new tests. T089/T090 closed only for no-device contracts and source-grounded matrix, not T081/nativeMac/T019/fullgoal. No production behavior was changed or live acceptance executed.


## Mac Shares/PCAP implemented — 2026-10-04

User requested integration, not removal of guards. Mac shared API now forwards collect=reactions/shares/both; local doctor and actual capabilities handler expose implementation support for PCAP. Native runner supports safe share-count-only navigation (no Share composer), source-locked fresh reopen after accepted Reaction phase, fresh Share min-line query guard, terminal/query/error/source validation and shared analyzer/API completeness. Shared analyzer adds optional --share-min-line (default1 preserves Windows); earlier same-source Share users/terminal pages excluded from new phase while target/Reaction proof remains. Share total/visible gap retained; empty result needs real terminal query, not zero UI rows. Reaction diagnostics and live progress remain in combined mode.

Android PCAP requires existing su0 and /system/bin/tcpdump, never Mac host sudo/Windows exe, no install/ADBrestart. Register unique path/PID marker before start; verify exact executable/run-specific path before signals, bounded SIGINT/SIGTERM stop, no signals to mismatched PID; preserve uncertain remote evidence and record cleanup failure in runner status for API lease protection. Stream SHA256 after pull, verify PCAP header + remote checksum, atomically publish raw-network.pcap and metadata; only then remove this run's two known remote files. Missing/corrupt requested PCAP cannot be successful capture. Cancellation143 calls cleanup and preserves partial status; signal/ownership/transport behavior in fixtures is not native Android proof.

Focused current122tests passed; existing default/Windows/analyzer tests plus active Mac tests. First syntax/legacy unsupported fixtures repaired before acceptance. Current full gate792tests/132.082s/OK skipped2/exit0, command streams and report read back at C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261004-mac-integration-01/. Same Windows runner byte snapshot unchanged; only narrow Mac argv/capability + opt-in analyzer behavior shared. Generated remote shell syntax passes GitBash -n (not remote execution). No real devices, services or login touched; 5037/7897 PID unchanged and 8765/8766/8081 remain unbound.

Nine public source/test/docs files committed/pushed ONLY existing test branch from isolated temp clone: ea522be0b9efa7fe3e5884f52e709d472d4e361b, CI run37168276769 currently running. Current dirty checkout/branch and main not switched/merged; no forcepush/release. Prior five Mac contract tests from earlier turn also included, old capability exclusions replaced by new contracts. Exact source ZIP C:/Users/lenovo/Desktop/facebook/dist/facebook-source-20261004-093311.zip:190entries/189manifest hashes verified, SHA256=293b5de5a64dc21fa55c9952b3b6a8a96c27610c8ac76e93fc0a3d4c59e44db7. SHA256SUMS is actually facebook/SHA256SUMS.txt; initial verifier's wrong filename fixed, final CRC/entry hash/code readback succeeded. ZIP is local handoff, not published release.

Rollback original source/tests/docs: C:/Users/lenovo/AppData/Local/Temp/facebook-mac-integration-20261004-091112/; turn-scoped implementation.diff/current-sha256.json preserved. Restore only changed files from that snapshot, not git reset/clean; service reload requires rechecking idle ownership. Main executor second-pass inspected source boundaries; NOT independently attributable whole-system review. Native Mac/real Android/Facebook GUI/ABI/CA/Frida/PCAP/Share visibility acceptance unavailable; T081/T019 full goal stay open. Current local implementation supersedes older "Mac Shares/PCAP unsupported" capability statements, not native acceptance gaps.


## Hosted CI closeout — 2026-10-04

Pushed isolated test branch commit `ea522be0b9efa7fe3e5884f52e709d472d4e361b`; GitHub Actions run `37168276769` completed success on both `windows-latest` and `macos-latest` (Windows 2026-10-04 09:35:01 CST, macOS 09:36:01 CST). CI validates source/fixture contracts only; it does not validate a real Mac login, Android root, Frida/CA, Facebook visibility or PCAP transfer. Current worktree remains on `codex/stabilization-20260915`; no merge, release, service restart, or device mutation.


## Review correction — 2026-10-04

The preceding claim that standalone Shares returned shares-terminal was incorrect. Source at ea522be already returns reaction_reason or terminal, so standalone Shares succeeds. Commit57fd2b0 added an unconditional terminal return after Shares; it was unnecessary for standalone and discarded the run_collection bounded Reaction return in combined mode. Existing final status separately retains pagination_stop_reason; no claim that an actual bounded capture was falsely published as full completion is established.

Three-revision isolated method replay confirms: ea522be standalone=terminal/combined_bounded=scroll-budget;57fd2b0 standalone=terminal/combined_bounded=terminal;165e358 restores original outcomes. Added bounded regression failed on57fd2b0, passes after removing its three-line change. Existing standalone regression retained. No other production behavior changed: current runner matches ea522be bytes after line-ending normalization. Evidence/backup C:/Users/lenovo/AppData/Local/Temp/facebook-review-correction-20261004-094504/ (outcome-evidence.json). Focused35 tests passed. Prior gate mac-integration-02 passed793/2skip but did not cover this bounded return, demonstrating why passing CI/local tests were insufficient evidence for that earlier claim.

Corrective test branch commit165e35862d15ffa6914c7c00760072363465ca18, current CI run37169028745. No forcepush/main merge or runtime/device changes. Full local correction gate and current CI awaiting readback; original Mac/Android/Facebook live acceptance and independent whole-system review remain unproven.


Correction gate readback: current full local suite794 tests/130.374s/OK skipped2, reportpassed and all command returncodes0 with original stream files checked. Current local handoff dist/facebook-source-20261004-094855.zip contains190 entries,189 verified SHA256 manifest rows; current runner/tests bytes checked. Archive SHA256962c08cb1cc0326dc7634c746039dba0406c5284563665eb8cfd43a39487c8f6. Older ZIPs remain historical, not removed. Current hosted CI37169028745 is still running at this ledger update; do not infer pass from earlier commits.


Correction hosted CI completed: run37169028745 at commit165e35862d15ffa6914c7c00760072363465ca18 succeeded for both Windows/macOS; gh watch exit0 and final job status readback confirmed. Current local full gate794/2skip and source ZIP094855 are the correction evidence. T096 closed for those checks/handoff only. No independent reviewer/native Mac/real Facebook acceptance is implied; overall maintenance goal remains active.


## PCAP producer lifecycle finding — 2026-10-04

Confirmed in a no-device fixture: stop_pcap initially accepted _pcap_state=stopped before requesting any signal, then published a valid PCAP/hash as finalized. This can conceal premature tcpdump exit; header/checksum do not prove duration. New test expecting rejection failed with CaptureError not raised on the old source. Minimal repair requires owned producer before controlled stop; unexpected exit fails with remote evidence retained. Existing cleanup continues restoring the owned proxy and execute marks exit2/completion_claim_allowed=false/partial_evidence_retained=true via cleanup_errors, retaining existing API cleanup protection. No actual crashed Facebook/device capture claimed.

Focused63 tests passed (Android runner + cancellation/transport); normal SIGINT/hash/publication still covered, mismatched PID/timeouts still fail. Test-branch804be917e4b9b0386e0df9096fd43990ca127f98 contains only runner six-line change plus tests; current CI37169519372 pending. Full local gate run-20261004-pcap-lifecycle-01 pending. Backup C:/Users/lenovo/AppData/Local/Temp/facebook-pcap-lifecycle-20261004-095433/. No new polling, shorter waits, automatic retries, Windows script change, live ADB calls, service restart, main merge or source release. Guard detects pre-stopped producer at finalization, not all packet drops/disk faults; no packet-loss-free guarantee.


## PCAP lifecycle current local verification — 2026-10-04

Current full gate run-20261004-pcap-lifecycle-01 passed796 tests/128.222s/OK skipped2/exit0. report.json and retained original stdout/stderr paths were reread, all command results verified exit0. Current archive C:/Users/lenovo/Desktop/facebook/dist/facebook-source-20261004-095810.zip:190 unique entries/189 SHA256 manifest rows, CRC pass, current runner/tests exact bytes verified; archive SHA256 d360eac587c726a974f4ba5cd7492d954adfbed42166cf87a2f99effc8c5295d. Windows CI37169519372 at804be917e4b9b0386e0df9096fd43990ca127f98 passed; macOS still running at this snapshot. No native acceptance inferred.


## PCAP lifecycle CI closeout — 2026-10-04

Current test-branch804be917e4b9b0386e0df9096fd43990ca127f98 run37169519372 completed success on both Windows and macOS. Final gh job readback confirms Windows completed2026-10-04 10:00:36+08:00/macOS10:01:02+08:00; watch exit0. Current local full gate796/2skip and ZIP095810 verified as above. T098 closed only for these declared code/fixture/CI/source-package scopes. Port checks retain proxy7897 PID11904/ADB5037 PID25244, no8765/8766/8081 listener; isolated test clone clean; no main merge/release/device/service changes. Current runner finalization rejects premature tcpdump exit, but packet loss, permission behavior and actual ARM64 Android/Mac runtime remain unaccepted without real-platform evidence. Overall goal/T019 and independent whole-review not completed.


## Confirmed Mac API/runner output handoff repair — 2026-10-04

Current real run_capture_job opens attempt/capture.log as stdout before Popen. Active Mac CaptureRunner.execute formerly rejected every nonempty output directory, including that legitimate API log. A real child process invoking the actual runner with mocked device/preflight/collection failed exit1 (--output-dir new or empty) on old code instead of the expected cancellation143. This is an API-startup contract defect, not a UI/permissions hypothesis.

Mac build_capture_command now forwards internal --api-log. Runner permits only sole regular non-symlink capture.log whose lstat dev/inode equals inherited stdout fstat dev/inode; other entries, wrong descriptor, empty API directory and standalone existing log remain rejected. Real API/Popen/redirected file/runner/cleanup/status test now exits143 and writes status exit143/completionfalse/cleanuperrors[]; real Facebook/device functions are mocked, Windows-only fcntl shim used in that child where necessary. No actual Android capture, nativeMac or storage durability proof claimed. Added ownership/mismatch/existing-file/symlink guards; focused84 passed with1skip because local Windows cannot create the temporary file symlink (macOS CI runs that case). Windows argv/script unchanged; no clearing API log or allowing arbitrary reused output directories.

Test branch current commit c676363d601da8dc89f4ea1f76f185a0834d438c, run37170610196 pending. Local full gate run-20261004-mac-api-log-01 pending. Backup C:/Users/lenovo/AppData/Local/Temp/facebook-mac-api-log-20261004-101442/; source changes3files reviewed in isolated clone, no reset/merge/runtime/device changes. Prior796tests/CI only establish earlier source; they did not exercise the API-owned stdout directory handoff.


## Windows CI timeout fixture correction — 2026-10-04

Current c676363 Mac API integration passed macOS CI run37170610196; Windows failure was tests.test_maintenance_gate.test_timeout_retains_output_and_is_failed temp-directory cleanup WinError32 on retained stderr log, not a Mac API test failure. Raw failing job111342633951 log retained externally. Live owned4second dummy process observation showed venv Python launcher parent51824 -> actual interpreter child16348; fixture exited naturally, no kill/production service call. Existing gate documents direct-child-only timeout, so original30second sleep via venv redirector incorrectly exercised an extra process and could hold inherited logs beyond parent termination.

Fixture now uses sys._base_executable (fallback sys.executable) only for the stdlib timeout helper, preserving1second deadline/timedout/failure/output assertions. Production gate unchanged; no new descendant kill or weakened cleanup assertion. Three repeat timeout cases plus entire maintenance_gate module29tests passed. Source production c676363 and fixture-only c7ba7d4af1366ae96288fdd4439ac85fff426498 pending currentCI37171037469/full local run-20261004-mac-api-log-02. Prior local800/3skip/125.533s passed and raw streams checked, but current fixture-only change has separate verification below. Third Windows skip is temporary symlink permission check; true native file/GUI/device acceptance not inferred.


## Mac API log handoff final verification — 2026-10-04

Current local gate800 tests/123.472s/OK skipped3/exit0, all original command streams and report read back at C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261004-mac-api-log-02/. Production API/runner fix c676363 plus timeout-fixture correction c7ba7d4af1366ae96288fdd4439ac85fff426498 passed both hosted Windows/macOS CI run37171037469 (Windows completed2026-10-04 10:29:29+08:00; Mac10:30:36+08:00). Test timeout cleanup failure from earlier run retained and no blanket retry/skip/production cleanup expansion used. New file-link guard test unavailable on local Windows but executed on macOS CI; three local skips not native business acceptance.

T100/T101 closed for actual API->Popen->stdout/runner/status fixture and declared local/CI/source handoff scope, not all-platform/capture success. These changes fix the default Mac Reaction entry too, not only new Shares. No real device/account/Frida/CA/bootstrap/version/proxy changes, main merge or release. Existing Windows command path is unchanged; gate production code unchanged. User-facing same API payload remains, internal --api-log is not a JSON API parameter. Whole independent review, broad performance matrix and native Mac business acceptance remain incomplete.
