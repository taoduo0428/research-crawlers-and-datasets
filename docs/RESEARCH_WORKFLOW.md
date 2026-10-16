
# 课题组使用流程

1. 项目负责人将成员按最小权限加入 Private 仓库；不得复制他人的浏览器登录状态。
2. 克隆仓库并运行 `git lfs pull`、`python tools/verify_release.py`。
3. 先选一个平台阅读该站 README，然后在对应 `source/` 中建立虚拟环境、安装依赖、执行离线测试。
4. 与网站通信的运行参数（账号、代理、配额、日期窗口）只写入本机独立配置，不上传。
5. 研究时先按 `record_id`、CID、ASIN/时间等正确主键合并，记录缺失与样本选择规则。
6. 分享研究成果时只提交脱敏数据、新生成的统计脚本、README 更新以及 `metadata` 校验；用户身份/原始抓包继续由获批研究者在本机保管。
7. 加入/更新大型数据：`git lfs track "projects/**/*.csv"`（仓库已配置）、`git add`、`git commit`、`git push`。源文件哈希清单应同时更新。
