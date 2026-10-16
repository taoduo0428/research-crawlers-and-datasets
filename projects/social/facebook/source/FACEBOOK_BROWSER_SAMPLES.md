# 浏览器与本地采集实测样本（2026-09-08）

当前项目状态和各 URL 的证据等级见 [2026-09-09 当前状态](CURRENT_STATUS_2026-09-09.md)。本文的浏览器样本与移动端 Probe 样本都要按 `initial-ready`、`partial/open-tail`、`terminal-complete` 区分；首屏出现用户不等于全量名单。

以下样本使用已登录 Edge 账号人工打开页面，并点击帖子级 Reaction 入口核验。只记录公开帖子地址、计数和可见性结论，不保存登录材料或用户姓名。浏览器能打开名单不等于 Android 已完成全量采集；两者分开记录。

## 浏览器可打开 Reaction 用户名单的正例

| 链接类型 | 可复测 URL | 页面观察 | 当前用途 |
| --- | --- | --- | --- |
| 普通数字帖子 | https://www.facebook.com/61563119350848/posts/122106880826437311 | All 3537、Like 2.4K；Reaction 弹窗显示多条用户条目 | 普通公开帖子正例 |
| 普通数字帖子 | https://www.facebook.com/100063690504939/posts/1691321539667513 | All 949、Like 910；Reaction 弹窗显示多条用户条目 | 普通公开帖子正例 |
| `share/p` | https://www.facebook.com/share/p/1DVLR4FNo6/ | 解析到 ATC Taiwan 的 `story.php` 帖子；All 949、Like 910；可打开用户名单 | 短链解析与 `story.php` 正例 |
| `share/p` | https://www.facebook.com/share/p/1QXj6TbPNv/ | 解析到群组 permalink；All 900、Like 432；可打开用户名单 | 短链解析到群组帖正例 |
| 群组帖子 | https://www.facebook.com/groups/1020765891821918/posts/2158908574674305/ | All 281、Like 242；Reaction 弹窗显示多条用户条目 | 群组多图帖子正例 |
| 群组帖子 | https://www.facebook.com/groups/132423725423951/permalink/1415834113749566/ | All 900、Like 432；Reaction 弹窗显示多条用户条目 | 群组帖子正例 |
| `pfbid` 帖子 | https://www.facebook.com/ATCTPE/posts/pfbid02qYWESeXuh3yj4oH6jHoy6xNKJLg8L777N5fFmjbZYNChhJ45ZE9S7CKtqKicdT7rl | All 949、Like 910；可打开用户名单 | `pfbid` 入口绑定正例 |
| `pfbid` 帖子 | https://www.facebook.com/IsmailiGoldenLight/posts/pfbid021Q7Q5Q5NNZHvYBMm87bJQcEG6ysr2vMX9gHsSiQMAk69N9DrKQ2yrMxtwRXDfF7Nl | All 3537、Like 2.4K；可打开用户名单 | `pfbid` 入口绑定正例 |
| Watch 视频 | https://www.facebook.com/watch/?v=1069930052107630 | All 687、Like 575；独立的 `Like: 575 people` 入口打开后显示多条用户条目 | Watch 视频正例 |
| Watch 视频 | https://www.facebook.com/watch/?v=1035807572392983 | All 604、Like 524；独立的 `Like: 524 people` 入口打开后显示多条用户条目 | Watch 视频正例 |

## Reel 与 `share/r` 样本

以下两个 `share/r` 都能解析到 Reel，并能看到点赞计数。当前 Edge 的 Reel viewer 将点赞数和 Like 操作合并为一个控件，没有像 Watch 页面一样暴露独立的“Like: N people”名单入口，因此它们是 Reel/短链入口正例，也是入口语义差异样本，不把它们误记为浏览器名单已打开正例。

| 链接 | 解析结果与观察 | 当前用途 |
| --- | --- | --- |
| https://www.facebook.com/share/r/1Dg59X6ysJ/ | 解析到 Reel `1054985397317048`；Android 首页探测 `initial-ready`，首屏 2 个用户 | Reel 短链与 Android 首屏正例；浏览器名单入口需按 Reel 布局单独处理 |
| https://www.facebook.com/share/r/19S8CA6Wko/ | 从 Watch 视频分享菜单取得；解析到 Reel `1069930052107630`；Reel viewer 可见点赞数，但 Like 操作与计数合并 | Reel 短链入口正例；不能套用 Watch 的名单按钮定位 |

## 浏览器可见但受到隐私限制的负例

| 帖子 | 页面观察 | 结论 |
| --- | --- | --- |
| https://www.facebook.com/scmp/posts/pfbid02cwtTB6SMNxjqvbB8BhfET3KQrJZQSjxnbzxfE1LZXMT2EtN3vPK78zvvJVnPVe9il | All 63；明确提示仅显示已点赞好友姓名 | 不能按总数宣称完整名单 |
| https://www.facebook.com/chinadaily/posts/pfbid0BLXGZ4puu1o5igYonBkhW3Zo1U1LqgELJhoU9iWorfhYuTSdvbck6c1sM2jKoHZVl | All 1148；同样仅显示已点赞好友姓名 | 不能按总数宣称完整名单 |

## 2026-09-08 本地 Android 首页探测

以下探测使用现有 `worker-01 / emulator-5554`，每个链接只打开首个 Reaction 列表，不翻第 2 页、不遍历分类。`initial-ready` 表示本次运行确认了目标帖子并读到首屏可见用户；不代表已经抓完全部用户。

| 输入 URL | 结果 | 证据 |
| --- | --- | --- |
| `https://www.facebook.com/61563119350848/posts/122106880826437311` | `initial-ready`，严格目标确认，首屏 31 个可见用户，无限流 | `runs/20260908-221817`；目标 post `122106880826437311`，Reaction 总数 3537 |
| `https://www.facebook.com/share/p/1DVLR4FNo6/` | `initial-ready`，短链解析到 `story.php`，首屏 26 个可见用户，无限流 | `runs/20260908-222003`；解析 post `1691321539667513`，Reaction 总数 949 |
| `https://www.facebook.com/share/r/1Dg59X6ysJ/` | `initial-ready`，短链解析到 Reel `1054985397317048`，首屏 2 个可见用户，无限流 | `runs/20260908-223036`；本次新鲜查询绑定 source post `122122050735339946` |
| `https://www.facebook.com/watch/?v=1069930052107630` | 严格目标确认失败，3 次均拒绝继续；未导入其他帖子数据 | `runs/20260908-222131`；当前 Watch URL 在移动抓包中没有形成可确认目标 |
| `https://www.facebook.com/groups/1020765891821918/posts/2158908574674305/` | 目标 ID 已确认，但移动端没有渲染可定位的 Reaction 总数入口，程序安全停止 | `runs/20260908-222458`；未盲目上滑，未产生用户名单 |
| `https://www.facebook.com/ATCTPE/posts/pfbid02qYWESeXuh3yj4oH6jHoy6xNKJLg8L777N5fFmjbZYNChhJ45ZE9S7CKtqKicdT7rl` | 目标已确认，但尚未找到稳定的 Reaction 数字入口 | `runs/20260908-224247`；浏览器可打开 Like 910 名单，不能归因于隐私限制 |

## 2026-09-08/09 移动端入口跟进

以下结果是在上述基线之后重新执行的低压力 ProbeOnly。每条链接只打开目标帖子的首个 Reaction 列表，不翻第 2 页、不遍历 Reaction 分类；`initial-ready` 只表示目标、入口和首屏用户均已确认。

| 输入 URL | 结果 | 证据 |
| --- | --- | --- |
| `https://www.facebook.com/ATCTPE/posts/pfbid02qYWESeXuh3yj4oH6jHoy6xNKJLg8L777N5fFmjbZYNChhJ45ZE9S7CKtqKicdT7rl` | `initial-ready`；定位到 `552`，首屏 35 个用户，未分页 | `runs/20260908-231139`；`reaction-list`，`queries=2` |
| `https://www.facebook.com/groups/1020765891821918/posts/2158908574674305/` | `initial-ready`；定位到 `281`，首屏 29 个用户，未分页 | `runs/20260908-231627`；群组多图帖子入口已确认 |
| `https://www.facebook.com/groups/132423725423951/permalink/1415834113749566/` | `initial-ready`；定位到 `900`，首屏 38 个用户，未分页 | `runs/20260908-231833`；source post `1415834113749566` 已匹配 |
| `https://www.facebook.com/reel/1069930052107630/` | `initial-ready`；定位到 `691 个心情`，首屏 20 个用户，未分页 | `runs/20260908-232309`；Reel viewer 入口已确认 |
| `https://www.facebook.com/watch/?v=1069930052107630` | `initial-ready`；在目标打开前归一化为 `https://www.facebook.com/reel/1069930052107630/`，定位到 `691 个心情`，首屏 20 个用户，未分页 | `runs/20260908-234531`；`method=watch-url-canonical-reel`，`page2_requests=0` |
| `https://www.facebook.com/IsmailiGoldenLight/posts/pfbid021Q7Q5Q5NNZHvYBMm87bJQcEG6ysr2vMX9gHsSiQMAk69N9DrKQ2yrMxtwRXDfF7Nl` | 最新复测为 `initial-ready`；Facebook 将原 token 规范化为另一个 `pfbid`，程序通过同一导航响应的稳定 post `122106880826437311`、author `61563119350848` 完成严格绑定；定位到 `3537`，首屏 31 个用户，未分页 | 历史入口失败：`runs/20260908-234726`、`runs/20260908-235743`、`runs/20260909-000558`；数字 URL 交叉验证：`runs/20260909-144451`；原始 `pfbid` 修复后验证：`runs/20260909-150431` |

这组结果说明入口定位已经覆盖普通数字帖、两个 `pfbid` 样本、群组多图、群组 permalink、Reel 和 Watch 原始链接。第二个 `pfbid` 的历史失败包含一次列表 loading 和两次入口缺失；2026-09-09 的新证据确认帖子当前可枚举，且暴露了 Facebook 会为同一稳定帖子返回不同规范 `pfbid` 的兼容问题。修复只接受精确导航请求中的唯一稳定帖子身份，不把普通 Feed 或其他帖子当成目标。

## 入口定位结论

- 普通数字帖子、`share/p`：已具备浏览器名单正例，Android 首页入口也已有成功证据。
- `pfbid`：ATCTPE 与 IsmailiGoldenLight 两个样本均已有 Android `initial-ready` 证据；后者验证了规范 token 变化时的稳定数字帖子绑定。具体页面仍需逐次通过目标、入口和首屏查询校验。
- 群组帖子：多图群组和 permalink 两种布局都已有 Android `initial-ready` 证据；仍需按目标确认和首屏用户条件进入正式分页。
- Watch：原始 `/watch/?v=` 已通过归一化复用 Reel 的已验证移动端详情入口；当前证据是首屏成功，不代表全量完成。
- `share/r`/Reel：至少一个短链已有 Android `initial-ready`；Reel viewer 的点赞控件与 Watch 不同，必须使用单独的 Reel 入口策略。

数量与可见性可能随时间和登录账号变化。正式全量任务仍需目标确认、精确 source post 匹配、分页终止和完整性检查全部通过。
