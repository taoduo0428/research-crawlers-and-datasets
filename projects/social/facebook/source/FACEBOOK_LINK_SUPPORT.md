# Facebook 链接支持矩阵

统一当前状态基准见 [CURRENT_STATUS_2026-09-09.md](CURRENT_STATUS_2026-09-09.md)。本矩阵中的“支持”可能只表示解析、目标确认或首屏 Probe；只有明确写出 `terminal-complete` 才表示通过终点分页验收。

更新时间：2026-09-09

## 结论

目前不能表述为“Facebook 各种类型的链接都已支持”。准确结论是：常用帖子链接、`pfbid`、`story.php`、`share/p`、`share/r`、群组帖子、Reel 和 Watch 路径已经进入同一套严格目标校验与抓取流程。下表同时区分全量/API 完成和仅验证目标加首屏名单的 ProbeOnly；其他类型仍需对应页面布局的实机样本。

API 的 `normalize_post_url()` 只校验绝对 HTTP(S) Facebook 域名（`facebook_capture_api.py:247-256`），因此“API 接受一个 URL”不等于“该 URL 能被严格识别并抓取”。真正的目标 URL 识别在 `resolve_facebook_share_url.py:13-65`、`open_facebook_post_readonly.ps1:218-226`、`run_facebook_capture.ps1:257-260` 和抓包后的 source-post 校验中完成。

## 已有真实实机证据

| 链接形式 | 当前等级 | 证据与说明 |
|---|---|---|
| `/<author>/posts/<numeric_id>` | 已实机、API 完成 | `api-jobs/fb_1eb1c96630dd462a.job.json` 为 `completed`，对应 `runs/api-fb_1eb1c96630dd462a`。文字/链接样本 `runs/20260905-110052` 严格匹配 post `1457763433066371` 并抓到正文与计数；Reaction 查询返回终态空 edges，因此只能表述为“当前账号不可枚举”，不能表述为名单抓取成功。 |
| `/<author>/posts/<pfbid...>` | 已实机、首屏可枚举 | `runs/20260908-231139` 的 ATCTPE 样本读取首屏 35 个用户；`runs/20260909-150431` 的 IsmailiGoldenLight 原始 URL 在 Facebook 更换规范 `pfbid` 后，仍严格绑定到 post `122106880826437311` 并读取首屏 31 个用户。两次均未进入分页。 |
| `story.php?story_fbid=<id>&id=<owner>` | 已实机、API 深抓完成 | 并发任务 `api-jobs/fb_a569eae0dfdf411e.job.json` 由 `worker-01 / emulator-5554` 完成；`runs/api-fb_a569eae0dfdf411e` 严格匹配 post `122106880826437311`，获得 3,538 个当前账号可见的唯一 Reaction 用户，143 页、零限流。Facebook 报告总数为 3,539，差 1 属于平台可见性/计数差异。 |
| `/share/p/<token>` | 已实机、API 完成 | `api-jobs/fb_76d0f595e4ac463f.job.json` 为 `completed`，对应批次 post-003；另有连续 `share/p` 完成任务。 |
| `/share/r/<token>` | 已实机、API 完成 | 并发任务 `api-jobs/fb_ede87700577a475b.job.json` 由 `worker-02 / emulator-5556` 完成；`runs/api-fb_ede87700577a475b` 严格匹配 source post `122122050735339946`，为 1 个用户、1 页、`terminal_page_seen=true`、零限流。 |
| `/reel/<numeric_id>` | 已实机、首屏可枚举 | `runs/20260908-232309` 直接 Reel 定位到 `691 个心情` 并读取首屏 20 个用户；另有上述 `share/r` 完成任务。 |
| `/watch/?v=<numeric_id>` | 已实机、首屏可枚举 | `runs/20260908-234531` 将原始 Watch URL 规范化到同 ID Reel，严格目标确认后读取首屏 20 个用户，`page2_requests=0`。 |
| `/groups/<group_id>/posts/<post_id>` | 已实机、首屏可枚举 | `runs/20260908-231627` 的群组多图帖子严格确认目标，定位到 `281` 并读取首屏 29 个用户。 |
| `/groups/<group_id>/permalink/<post_id>` | 已实机、首屏可枚举 | `runs/20260908-231833` 匹配 source post `1415834113749566`，定位到 `900` 并读取首屏 38 个用户。 |

## 双 worker 隔离验收

2026-09-05 的并发验收同时提交 Reel 与 `story.php` 两个真实任务。Reel 固定落到 `worker-02 / emulator-5556 / capture 18082 / native 18767 / runs/api-fb_ede87700577a475b`；Photo 固定落到 `worker-01 / emulator-5554 / capture 8081 / native 18766 / runs/api-fb_a569eae0dfdf411e`。两个分析 CSV 各自只包含目标 source post id，没有跨任务记录。

服务重启后，`GET /v1/jobs/<id>/result` 能重新加载以上两个完成任务。实时 `/health` 显示 worker-01 与 worker-02 均为 `idle/device`；ADB reverse 分别为 `7897 + 8081` 和 `7897 + 18082`，worker-02 没有 `8081`。`worker-03 / emulator-5558` 始终为 `disabled/offline`，未安装 Facebook，也未复制或修改账号数据。

模拟器必须通过 `launch_visible_in_user_session.ps1 -WorkerId worker-01|worker-02` 在 Windows 交互会话中启动，再由 `start_facebook_stable.ps1` 完成对应端口的网络初始化。这样 qemu 不会随短生命周期的后台命令结束而被回收。

## 代码与单元测试支持，但尚未逐类实机验收

| 链接形式 | 当前证据 | 尚缺证据 |
|---|---|---|
| `/permalink/<post_id>` | 严格目标识别正则接受该形式（`run_facebook_capture.ps1:257-260`）。 | owner/group 上下文与真实 source-post 匹配的实机证明。 |
| `/videos/<numeric_id>` | canonical 解析在 `resolve_facebook_share_url.py:29-39`；分析器 token 测试覆盖视频路径。 | Facebook App 视频详情布局上的实机 Reaction/Share 任务。 |
| `/share/v/<token>` | opener/analyzer 的短链识别正则包含 `p|r|v`（`open_facebook_post_readonly.ps1:226`、`analyze_facebook_capture.py:166`）。 | 专用单测、真实重定向和完整抓取证据；当前不作为正式支持承诺。 |

## 明确不能作为通用支持承诺

- `/photo/?fbid=...`、`photo.php`：这是媒体 URL，不足以证明目标帖子身份；不能用媒体 id 冒充 source post id。
- 普通主页、Feed、搜索结果、评论锚点或非帖子 URL：可能被 Facebook 打开，但没有唯一目标帖子身份，不进入“抓取成功”判定。
- 已删除、私密、地区/年龄限制、账号无权限、登录门控页面：脚本不能越过 Facebook 的访问控制。
- 页面不暴露 Reaction 用户列表：只能报告不可枚举，不能虚构用户列表。
- Share 用户：仍受具体 UI、可见子集与当前 `ResharesOfContentFeedQuery` 形状影响；`terminal=true` 只表示当前账号可见列表到终点，不代表 Facebook 全量用户。
- URL 能跳转但抓包 source post 与目标不一致：严格判失败，不会把别的帖子结果算作成功。

## 升级为“正式支持”的验收门槛

每一种新链接形式至少需要：canonical 解析单测、Android 打开后的新鲜目标边界、抓包中的精确 source post 匹配、一次真实 API 完成任务，以及 Reaction/Share 对应的终止或不可见语义。缺少任一项时，只能标为“代码支持”或“待实机”。
