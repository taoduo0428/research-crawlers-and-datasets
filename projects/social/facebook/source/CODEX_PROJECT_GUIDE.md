# 已有 Codex 的项目接手配置

这是旧开发环境里 Codex、Serena、RTK 等工具的配置笔记。它不是运行 Facebook 爬虫的前置条件；普通使用者直接看 [项目 README](README.md) 和 [Windows 安装说明](WINDOWS_DEPLOYMENT.md) 就行。下面记录的版本号、路径以写作当时为准。
无需重新安装 Codex。本机核对时 CLI 为 `0.147.0`，`codex login status` 显示 ChatGPT 登录，RTK 为 `0.49.0`；Serena 已成功激活本项目并执行符号查询。这里只记录可迁移用法，不复制本机账户或完整配置。

## 路径和优先级

| 内容 | Windows 默认位置 | macOS 默认位置 |
| --- | --- | --- |
| 用户配置 | `%USERPROFILE%\.codex\config.toml` | `~/.codex/config.toml` |
| 用户指令 | `%USERPROFILE%\.codex\AGENTS.md` | `~/.codex/AGENTS.md` |
| 项目配置 | `<repo>/.codex/config.toml` | 相同 |
| 项目指令 | 本仓库 [AGENTS.md](AGENTS.md) | 相同 |
| 用户 Skills | `%USERPROFILE%\.agents\skills` | `~/.agents/skills` |
| 项目 Skills | `<repo>/.agents/skills` | 相同 |

若设置 CODEX_HOME，用户配置根目录随之变化。受信任项目可加载项目配置，命令行临时设置优先；不要把未信任项目的配置当成已经生效。[配置文档](https://developers.openai.com/codex/config-basic/)。AGENTS 从全局到仓库、当前子目录逐层适用，同级 `AGENTS.override.md` 可覆盖常规文件。[指令文档](https://developers.openai.com/codex/guides/agents-md/)。

当前机器另有 `~/.codex/skills` 下的历史技能，并由会话显式列出；新接收者以自己 Codex 的实际技能目录和清单为准，不能仅复制路径就认为加载成功。[Skills 文档](https://developers.openai.com/codex/skills/)。项目建议使用 `system-architect` 和 `windows-macos-dev-guard`，它们用于限定修改与核对平台差异，不要求全仓库重构。

## Serena 与 RTK

先检查现有 `codex mcp list`，已配置 Serena 就直接使用，避免重复添加。需要新增时，确认 `serena` 在 PATH 中，再执行已核对的 STDIO 命令结构：

```text
codex mcp add serena -- serena start-mcp-server --project-from-cwd --context codex --enable-web-dashboard false --open-web-dashboard false
```

等价最小 TOML（手动合并已有文件，不整体覆盖）：

```toml
[mcp_servers.serena]
command = "serena"
args = ["start-mcp-server", "--project-from-cwd", "--context", "codex", "--enable-web-dashboard", "false", "--open-web-dashboard", "false"]
```

`command`、`args` 属于 Codex MCP 支持的配置项。[MCP 文档](https://developers.openai.com/codex/mcp/)。若工具不在 PATH，填写接收者自己机器的可执行文件路径；不要复制本机 Windows hidden-stdio 包装器、盘符、自定义 provider 或授权配置到 Mac。进入本仓库后激活项目，再用符号定位/引用查询；已知文件和文本用定向 `rg`，不重复全文读取。

RTK 用于压缩输出，非项目运行依赖；可执行 `rtk --version`、`rtk git status`、`rtk git diff --stat`。本机通过 `~/.local/bin/rtk.exe` 调用；Mac 使用自己的 `rtk`。出现未安装 hook 的提示不影响这些手工命令，不需要为本项目自动安装全局 hook。原始失败信息需要时再用普通命令取证。

修改真实 Codex 配置前，先做带时间戳的回滚副本并在同目录 `CODEX-CHANGELOG.md` 记录原因/差异；本次交付只新增项目文档和指令，没有改变用户 Codex 配置、登录或 MCP 进程。

## 登录与切换账号

先 `codex login status`；需要切换时，在没有进行中的操作后运行 `codex logout`，再运行 `codex login` 通过浏览器登录目标 ChatGPT 账号。API Key 登录则从环境变量经标准输入传入，不把值写入命令参数或仓库：

```powershell
# Windows：先在当前会话设置自己的 OPENAI_API_KEY，以下命令不包含密钥字面值。
$env:OPENAI_API_KEY | codex login --with-api-key
codex login status
```

```sh
# macOS
printenv OPENAI_API_KEY | codex login --with-api-key
codex login status
```

这属于主动切换凭据，不要由自动部署替用户执行。API 计费与 ChatGPT 订阅方式不同。凭据可能由本机认证文件或系统凭据库管理；不要读取/分享 auth.json 内容。[认证文档](https://developers.openai.com/codex/auth/)。桌面应用在其账户界面确认实际登录状态，不假设 CLI 切换后界面一定同步。

账号切换不需要删除仓库、`.git`、AGENTS、会话历史或模拟器数据。GitHub 的 `gh auth status` / Git credential 与 Codex 登录独立；切换 Codex 后先分别检查，再继续原分支工作。

## 节省额度的接手顺序

读 AGENTS 和 [稳定基线](STABLE_BASELINE.md)，检查 Git 状态及相关服务所有者；按一个调用链定位问题；只读相关函数；做窄修改并跑对应测试。真实 URL 已通过且本次没有触及该链路时，不重复压力采集。证据不足的优化保持关闭；保留时间做 diff、测试与授权范围内的 commit/push。勿清理其他任务的未提交改动、历史或账号环境。
