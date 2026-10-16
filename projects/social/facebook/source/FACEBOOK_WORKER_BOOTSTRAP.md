# Facebook 模拟器 Worker 一键新增说明

当前项目总状态见 [CURRENT_STATUS_2026-09-09.md](CURRENT_STATUS_2026-09-09.md)。下面的 worker-03 信息是 2026-09-05 历史实例记录；截至 2026-09-09 仍不能把 `awaiting-login` 当成已完成实机验收。

## 一键新增

双击根目录的 `add_facebook_worker.cmd`。默认执行 `Add`，会自动：

1. 分配下一个 `worker-NN`、干净 AVD 名称、未占用的偶数 emulator serial、独立 capture/native 端口。
2. 先以 `enabled=false` 原子写入 `facebook_workers.json`，并在 `worker-bootstrap/backups` 保存注册表回滚副本。
3. 从 Android 35 Google APIs 系统镜像创建全新 AVD，不复制任何旧 AVD userdata。
4. 从一台在线源 worker 只提取 Facebook APK 和 `/data/local/tmp/fb-fs17`，不复制账号数据、Cookie、Token 或应用私有目录。
5. 安装 UI dump helper，配置全局代理、ADB reverse 和独立抓取端口。
6. 安装 mitmproxy CA 到 writable-system overlay，并挂载/验证 Android 15 Conscrypt 证书库；此后该 worker 的自动启动会始终重新打开 overlay 并恢复运行时挂载，同时启用 Facebook 专用 Frida SSL unpin。
7. 打开 Facebook 登录页，并停在 `awaiting-login`；此时调度器不能给新 worker 分配任务。

Facebook 登录、密码、验证码和账号授权必须由用户在新模拟器窗口中手动完成。

## 登录后验收并启用

以 `worker-03` 为例：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\add_facebook_worker.ps1 -Action Finalize -WorkerId worker-03
```

`Finalize` 会先检查设备、Facebook、UI helper、Frida、系统/Conscrypt CA、代理和 reverse 映射，再运行一次低频 `ReactionProbeOnly`。只有 Probe 成功后才启用 worker。可以用 `-ProbeUrl` 替换默认的已验证测试链接。

## 失败续跑

失败时不会删除 AVD，也不会启用 worker。修复提示中的环境问题后执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\add_facebook_worker.ps1 -Action Resume -WorkerId worker-03
```

所有步骤均按当前状态幂等检查；日志在 `worker-bootstrap/logs`，结构化阶段状态在 `worker-bootstrap/worker-NN.json`。

## 查看状态

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\add_facebook_worker.ps1 -Action Status
```

## 当前实例

2026-09-05 已用该流程创建并验证：

- `worker-03`
- AVD：`fb-x86_64-api35-worker-03`
- serial：`emulator-5558`
- capture port：`18083`
- native port：`18768`
- CA hash：`c8750f0d`
- 当前状态：`awaiting-login`、`enabled=false`

该实例仍需使用者手动登录 Facebook，再按上面的 `Finalize` 执行设备、代理、CA、Frida、UI 和低频 Probe 检查。没有新的终端输出或真实运行产物前，不应把它写成已启用或已完成 Reaction 全量采集。
