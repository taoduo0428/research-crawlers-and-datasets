# 猎聘详情补采：Edge 扩展

这是猎聘项目用过的一个**本地补详情工具**。Edge 扩展负责从页面读取公开的岗位描述，把结果交给本机 Python bridge。它不是普通的浏览器插件，离开本地服务无法单独工作。

## 使用顺序

1. 在 `projects/market-platform/` 目录启动本地 bridge：

   ```bash
   python tools/run_liepin_edge_extension_bridge.py --port 8765
   ```

2. 打开 Edge 的 `edge://extensions`，开启「开发人员模式」，选择「加载解压缩的扩展」，指向本文件夹。
3. 打开扩展设置。把本机 `.crawler-state/liepin-edge-extension/bridge-token` 中的**当前运行 token**填进去并保存。它是本机临时凭据，不要复制到 GitHub。
4. 保持正常 Edge 窗口打开。扩展会为任务建立后台标签页，并向本地 bridge 取任务、回传结果。

历史代码里的 worker ID 是 `browser_forward`、`api_reverse` 和 `camoufox_reverse_2`。`GET /next`、`POST /result` 都要求请求头 `X-Bridge-Token`。停掉本地 bridge 就不会再领取新任务。

这个组件只读取详情页 DOM，不应该读取或保存 Cookie、账号密码。遇到安全验证页时会报告 `security_transit`，不会自动绕过。当前页面结构是否仍然适用，需要先小样本测试。
